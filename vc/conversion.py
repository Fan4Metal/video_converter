"""Очередь конвертации: сборка аргументов ffmpeg, запуск и разбор прогресса."""
import os
import re
import subprocess
import sys
import threading
import time
import winsound

import wx

from vc.ffmpeg_tools import get_audio_bitrate
from vc.media_probe import get_audio_channels, get_hdr_info, get_video_info
from vc.resources import FFMPEG_PATH, get_resource_path
from vc.settings import RowSettings
from vc.utils import copy_mp4_tags, format_time, human_size, unique_output_path
from vc.widgets import CheckListCombo


class ConversionMixin:
    """Примесь к VideoConverter: очередь, ffmpeg, пропуск и отмена конвертации."""
    def on_convert(self, event):
        if self.probing:
            self.cancel_probe()
            return
        if self.converting:
            self.cancel_conversion()
            return

        if not self.row_widgets:
            self.log.AppendText("\n⚠ Нет файлов в очереди.\n")
            return

        self.refresh_all_estimates()
        self.all_jobs_duration = sum(float(w.get("duration") or 0.0) for w in self.row_widgets.values())
        self.done_duration = 0.0
        self.cancel_event.clear()
        self.skip_event.clear()
        self._current_uid = None
        self._queue_row = -1
        for w in self.row_widgets.values():
            w["skip"] = False
        self.converting = True

        self.btn_start.SetLabel("⏹ Отмена")
        self.progress.SetValue(0)
        self.progress_label.SetLabel("Прогресс: 0%")
        self.log.AppendText(f"{'-' * 30}\n▶ Запуск очереди...\n")

        self.disable_interface()

        self.queue_thread = threading.Thread(target=self.queue_worker, daemon=True)
        self.queue_thread.start()

    def queue_worker(self):
        self.current_output_file = None
        try:
            # Обход по индексу без снимка: во время конвертации удаление и
            # сортировка заблокированы, а новые строки добавляются только в конец
            # и подхватываются очередью.
            row = -1
            while row + 1 < len(self.row_order):
                row += 1
                uid = self.row_order[row]
                self._queue_row = row
                if self.cancel_event.is_set():
                    break

                widgets = self.row_widgets.get(uid)
                if not widgets:
                    continue
                path = widgets.get("path")
                if widgets.get("skip"):
                    wx.CallAfter(self.log.AppendText, f"\n⏭ Пропущен: {path}\n")
                    self.done_duration += float(widgets.get("duration") or 0.0)
                    continue
                duration = float(widgets.get("duration") or 0.0)
                gauge: wx.Gauge | None = widgets.get("gauge")
                if not path or not os.path.isfile(path):
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "❌ Нет файла")
                    if gauge:
                        wx.CallAfter(gauge.SetValue, 0)
                    continue

                # Пустой список дорожек — выходной файл без аудио.
                audio_tracks: list[tuple[int, int, str]] = []
                for track_index in self.selected_audio_tracks(widgets):
                    audio_channels = get_audio_channels(path, track_index)
                    audio_tracks.append((track_index, audio_channels, get_audio_bitrate(audio_channels)))
                output_file = unique_output_path(self.save_folder, path, self.toggle_suffix.GetValue())
                selected_subtitles = self.get_selected_subtitles(widgets) if self.chk_save_subtitles.GetValue() else []

                wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "⏳ Конвертация...")
                if gauge:
                    wx.CallAfter(gauge.SetValue, 0)

                wx.CallAfter(self.log.AppendText, f"\n{'-' * 30}\nНачало конвертации...\n🎬 Файл: {path}\n➡ Выход: {output_file}\n")
                self.current_output_file = output_file
                self.skip_event.clear()
                self._current_uid = uid
                wx.CallAfter(self._set_row_widgets_enabled, widgets, False)

                predicted_size = int(widgets.get("est_bytes") or 0)
                if predicted_size:
                    wx.CallAfter(self.log.AppendText, f"💾 Ожидаемый размер: {human_size(predicted_size)}\n")

                settings = widgets["settings"]

                ok = self.run_ffmpeg_with_progress(
                    input_path=path,
                    output_path=output_file,
                    audio_tracks=audio_tracks,
                    selected_subtitles=selected_subtitles,
                    duration=duration,
                    gauge=gauge,
                    settings=settings,
                    video_info=widgets.get("info") or {},
                    row=row,
                    widgets=widgets,
                )

                if ok and not self.cancel_event.is_set():
                    widgets.update({"output_file": output_file})
                    if self.chk_copy_tags.GetValue() and os.path.splitext(path)[1].lower() == ".mp4":
                        tags_ok, tags_err = copy_mp4_tags(path, output_file)
                        if tags_ok:
                            wx.CallAfter(self.log.AppendText, "📌 Теги скопированы\n")
                        else:
                            wx.CallAfter(self.log.AppendText, f"⚠ Не удалось скопировать теги: {tags_err}\n")
                    # Размер читаем после записи тегов — они меняют файл.
                    try:
                        actual_size = os.path.getsize(output_file)
                    except OSError:
                        actual_size = 0
                    if actual_size > 0:
                        widgets["est_bytes"] = actual_size
                        wx.CallAfter(self.list.SetStringItem, row, self.COL_EST, human_size(actual_size))
                        if predicted_size:
                            wx.CallAfter(
                                self.log.AppendText,
                                f"💾 Размер: {human_size(actual_size)} (прогноз: {human_size(predicted_size)})\n",
                            )
                        else:
                            wx.CallAfter(self.log.AppendText, f"💾 Размер: {human_size(actual_size)}\n")
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "✅ Готово")
                    wx.CallAfter(gauge.SetValue, 100)
                    wx.CallAfter(self.log.AppendText, "\n ✅ Конвертация завершена\n")
                    self.done_duration += duration
                elif self.skip_event.is_set() and not self.cancel_event.is_set():
                    self.skip_event.clear()
                    self._remove_partial_output(output_file)
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "⏭ Пропущен")
                    self._restore_row_estimate(row, widgets, predicted_size)
                    wx.CallAfter(gauge.SetValue, 0)
                    wx.CallAfter(self.log.AppendText, "⏭ Файл пропущен\n")
                    self.done_duration += duration
                elif self.cancel_event.is_set():
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "⏹ Отменено")
                    self._restore_row_estimate(row, widgets, predicted_size)
                    wx.CallAfter(gauge.SetValue, 100)
                    break
                else:
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "❌ Ошибка")
                    self._restore_row_estimate(row, widgets, predicted_size)
                    self.done_duration += duration

            if self.cancel_event.is_set():
                wx.CallAfter(self.progress_label.SetLabel, "⏹ Очередь остановлена пользователем")
            else:
                wx.CallAfter(self.progress.SetValue, 100)
                wx.CallAfter(self.progress_label.SetLabel, "✅ Очередь завершена")
                winsound.PlaySound(get_resource_path("sound.wav"), winsound.SND_FILENAME | winsound.SND_ASYNC)

        finally:
            self.converting = False
            self.process = None
            wx.CallAfter(self.btn_start.SetLabel, "▶ Начать конвертацию")
            wx.CallAfter(self.progress.SetValue, 0)
            wx.CallAfter(self.enable_interface)

    def get_selected_subtitles(self, widgets: dict) -> list[dict]:
        subtitle_list: CheckListCombo | None = widgets.get("subtitles")
        subtitle_tracks = widgets.get("subtitle_tracks") or []
        if not subtitle_list:
            return []

        selected: list[dict] = []
        skipped: list[str] = []
        checked_items = set(subtitle_list.GetCheckedItems())
        for i, track in enumerate(subtitle_tracks):
            if i not in checked_items:
                continue
            if track.get("supported", False):
                selected.append(track)
            else:
                skipped.append(track.get("display", str(track.get("order", i))))

        for item in skipped:
            wx.CallAfter(self.log.AppendText, f"⚠ Субтитры пропущены, MP4 не поддерживает: {item}\n")

        return selected

    # --- FFmpeg ---
    def _resolve_effective_settings(self, settings: RowSettings) -> RowSettings:
        """
        Возвращает действующие настройки кодирования: либо настройки строки
        (если они не «глобальные»), либо текущие значения панели управления.
        """
        return settings if not settings.is_global else self.get_current_settings()

    def _build_audio_args(
        self, skip_audio: bool, audio_tracks: list[tuple[int, int, str]], video_info: dict
    ) -> tuple[list[str], list[str]]:
        """
        Возвращает (codec_args, metadata_args) для аудио. audio_tracks — список
        (индекс a:N, каналы, битрейт), основная дорожка первая; пустой список —
        без аудио. Для одной дорожки аргументы прежние; для нескольких — параметры
        и метаданные (язык, название, флаг default) задаются по каждому выходному потоку.
        """
        if not audio_tracks:
            wx.CallAfter(self.log.AppendText, "🎵 Аудио: нет\n")
            return ["-an"], []
        if len(audio_tracks) == 1:
            _, audio_channels, bitrate = audio_tracks[0]
            if skip_audio:
                wx.CallAfter(self.log.AppendText, "🎵 Аудио: copy\n")
                return ["-c:a", "copy"], []
            wx.CallAfter(self.log.AppendText, f"🎵 Аудио: AAC, {audio_channels}ch, {bitrate}\n")
            return ["-c:a", "aac", "-ac", str(audio_channels), "-b:a", bitrate], []

        audio_streams = video_info.get("audio_streams") or []
        codec_args: list[str] = ["-c:a", "copy"] if skip_audio else []
        metadata_args: list[str] = []
        for out_index, (track_index, audio_channels, bitrate) in enumerate(audio_tracks):
            if skip_audio:
                wx.CallAfter(self.log.AppendText, f"🎵 Аудио #{track_index}: copy\n")
            else:
                codec_args += [f"-c:a:{out_index}", "aac", f"-ac:a:{out_index}", str(audio_channels), f"-b:a:{out_index}", bitrate]
                wx.CallAfter(self.log.AppendText, f"🎵 Аудио #{track_index}: AAC, {audio_channels}ch, {bitrate}\n")
            stream = audio_streams[track_index] if 0 <= track_index < len(audio_streams) else {}
            language = str(stream.get("language") or "und")
            title = str(stream.get("title") or "").strip()
            metadata_args += [f"-metadata:s:a:{out_index}", f"language={language}"]
            if title:
                metadata_args += [f"-metadata:s:a:{out_index}", f"title={title}", f"-metadata:s:a:{out_index}", f"handler_name={title}"]
            metadata_args += [f"-disposition:a:{out_index}", "default" if out_index == 0 else "0"]
        return codec_args, metadata_args

    def _build_subtitle_args(self, selected_subtitles: list[dict]) -> tuple[list[str], list[str], list[str]]:
        """Возвращает (map_args, codec_args, metadata_args) для субтитров."""
        subtitle_map_args: list[str] = []
        subtitle_codec_args: list[str] = ["-sn"]
        subtitle_metadata_args: list[str] = []
        if selected_subtitles:
            for output_subtitle_index, track in enumerate(selected_subtitles):
                subtitle_map_args.extend(["-map", f"0:s:{track['order']}"])
                language = str(track.get("language") or "und")
                title = str(track.get("title") or "").strip()
                if language:
                    subtitle_metadata_args.extend([f"-metadata:s:s:{output_subtitle_index}", f"language={language}"])
                if title:
                    subtitle_metadata_args.extend([f"-metadata:s:s:{output_subtitle_index}", f"title={title}"])
                    subtitle_metadata_args.extend([f"-metadata:s:s:{output_subtitle_index}", f"handler_name={title}"])
            subtitle_codec_args = ["-c:s", "mov_text"]
            wx.CallAfter(self.log.AppendText, f"💬 Субтитры: {len(selected_subtitles)} дорожк(и), mov_text\n")
        else:
            wx.CallAfter(self.log.AppendText, "💬 Субтитры: нет\n")
        return subtitle_map_args, subtitle_codec_args, subtitle_metadata_args

    def _build_video_args(
        self,
        input_path: str,
        video_info: dict,
        skip_video: bool,
        encode_mode: int,
        qp_slider: int,
        limit_res: bool,
        tonemap_mode: int,
    ) -> list[str]:
        """
        Возвращает аргументы видео для ffmpeg: либо ["-c:v", "copy"], либо
        полный набор фильтров/энкодера (NVENC или CPU-фолбэк).
        """
        if skip_video:
            wx.CallAfter(self.log.AppendText, "🎥 Видео: copy\n")
            return ["-c:v", "copy"]

        # Используем данные, собранные при добавлении файла (кэш), без повторного запуска ffprobe.
        if "requires_tonemap" in video_info:
            hdr_type = video_info.get("hdr_type") or "SDR"
            auto_tonemap = bool(video_info.get("requires_tonemap"))
        else:
            hdr = get_hdr_info(input_path)
            hdr_type = hdr["type"]
            auto_tonemap = bool(hdr["requires_tonemap"])

        if tonemap_mode == 2:
            needs_tonemap = False
        elif tonemap_mode == 1:
            needs_tonemap = True
        else:
            needs_tonemap = auto_tonemap

        wx.CallAfter(self.log.AppendText, f"🎨 Видео: {hdr_type}, tonemap={'on' if needs_tonemap else 'off'}\n")

        scale_filter = ""
        if limit_res:
            try:
                w = int(video_info.get("width") or 0)
                h = int(video_info.get("height") or 0)
            except Exception:
                w, h = 0, 0
            if not (w and h):
                vinfo = get_video_info(input_path)
                try:
                    w = int(vinfo.get("width") or 0)
                    h = int(vinfo.get("height") or 0)
                except Exception:
                    w, h = 0, 0
            if w > 1920 or h > 1080:
                scale_filter = ",scale='if(gt(iw,1920),1920,iw):if(gt(ih,1080),1080,ih):force_original_aspect_ratio=decrease'"

        if needs_tonemap:
            vf_filter = (
                "zscale=t=linear:npl=30,format=gbrpf32le,"
                "zscale=p=bt709,tonemap=hable:param=1.5:desat=0,"
                "zscale=t=bt709:m=bt709:r=pc,format=yuv420p"
                f"{scale_filter}"
            )
        else:
            vf_filter = f"format=yuv420p{scale_filter}"

        if self.nvenc_available:
            if encode_mode == 0:
                rc_args = ["-rc", "vbr", "-cq", str(qp_slider), "-b:v", "0", "-qmin", "0"]
                wx.CallAfter(self.log.AppendText, f"🎯 Режим: NVENC, QP={qp_slider}\n")
            else:
                target_bitrate = f"{int(qp_slider * 1000)}k"
                rc_args = ["-b:v", target_bitrate, "-maxrate", target_bitrate, "-bufsize", "2M"]
                wx.CallAfter(self.log.AppendText, f"📦 Режим: NVENC, CBR={target_bitrate}\n")
            video_encoder_args = [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p4",
                *rc_args,
                "-profile:v",
                "high",
                "-tune",
                "hq",
                "-b_ref_mode",
                "middle",
                "-spatial_aq",
                "1",
            ]
        else:
            # Программный фолбэк на CPU, если аппаратный NVENC недоступен.
            if encode_mode == 0:
                rc_args = ["-crf", str(qp_slider)]
                wx.CallAfter(self.log.AppendText, f"🎯 Режим: CPU (libx264), CRF={qp_slider}\n")
            else:
                target_bitrate = f"{int(qp_slider * 1000)}k"
                rc_args = ["-b:v", target_bitrate, "-maxrate", target_bitrate, "-bufsize", "2M"]
                wx.CallAfter(self.log.AppendText, f"📦 Режим: CPU (libx264), CBR={target_bitrate}\n")
            video_encoder_args = [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                *rc_args,
                "-profile:v",
                "high",
            ]

        return ["-pix_fmt", "yuv420p", "-vf", vf_filter, *video_encoder_args]

    def run_ffmpeg_with_progress(
        self,
        input_path: str,
        output_path: str,
        audio_tracks: list[tuple[int, int, str]],
        selected_subtitles: list[dict],
        duration: float,
        gauge: wx.Gauge | None,
        settings: RowSettings,
        video_info: dict | None = None,
        row: int = -1,
        widgets: dict | None = None,
    ) -> bool:
        video_info = video_info or {}
        eff = self._resolve_effective_settings(settings)

        audio_map_args = [arg for track_index, _, _ in audio_tracks for arg in ("-map", f"0:a:{track_index}")]
        audio_codec_args, audio_metadata_args = self._build_audio_args(eff.skip_audio, audio_tracks, video_info)
        subtitle_map_args, subtitle_codec_args, subtitle_metadata_args = self._build_subtitle_args(selected_subtitles)
        video_args = self._build_video_args(
            input_path=input_path,
            video_info=video_info,
            skip_video=eff.skip_video,
            encode_mode=eff.encode_mode,
            qp_slider=eff.quality,
            limit_res=eff.limit_res,
            tonemap_mode=eff.tonemapping,
        )

        cmd = [
            FFMPEG_PATH,
            "-hide_banner",
            "-y",
            "-i",
            input_path,
            "-map",
            "0:v:0",
            *audio_map_args,
            *subtitle_map_args,
            *video_args,
            *audio_codec_args,
            "-map_metadata",
            "-1",
            *audio_metadata_args,
            *subtitle_metadata_args,
            "-bsf:v",  # удаление скрытых субтитров (Closed captions EIA-608/CEA-608)
            "filter_units=remove_types=6",
            *subtitle_codec_args,
            output_path,
        ]
        try:
            self.process = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            wx.CallAfter(self.log.AppendText, f"❌ Не удалось запустить ffmpeg: {e}\n")
            return False

        total_duration = max(float(duration or 0.0), 1.0)
        time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        speed_regex = re.compile(r"speed=\s*([\d\.]+)x")
        fps_regex = re.compile(r"fps=\s*([\d\.]+)")
        size_regex = re.compile(r"size=\s*(\d+)\s*([kKmM])?i?B")

        current_speed = "?"
        current_fps = "?"
        projected_size = 0
        # Живой прогноз включаем, только когда закодировано достаточно для осмысленной экстраполяции.
        min_time_for_estimate = max(5.0, total_duration * 0.02)

        for line in self.process.stderr:
            if self.cancel_event.is_set() or self.skip_event.is_set():
                break

            if self.chk_debug.GetValue():
                wx.CallAfter(self.log.AppendText, line)

            m = time_regex.search(line)
            if not m:
                continue

            h, mm, ss = m.groups()
            current_time = int(h) * 3600 + int(mm) * 60 + float(ss)

            row_progress = min(int(current_time / total_duration * 100), 100)

            overall = self.done_duration + current_time
            if self.all_jobs_duration > 0:
                overall_progress = min(int(overall / self.all_jobs_duration * 100), 100)
            else:
                overall_progress = row_progress

            sm = speed_regex.search(line)
            if sm:
                current_speed = sm.group(1)
            fm = fps_regex.search(line)
            if fm:
                current_fps = fm.group(1)

            # Прогноз итогового размера по уже записанным байтам.
            zm = size_regex.search(line)
            if zm and current_time >= min_time_for_estimate:
                unit = (zm.group(2) or "").lower()
                multiplier = 1024 if unit == "k" else (1024 * 1024 if unit == "m" else 1)
                encoded_bytes = int(zm.group(1)) * multiplier
                if encoded_bytes > 0:
                    projected_size = int(encoded_bytes * total_duration / current_time)
                    if widgets is not None:
                        widgets["est_bytes"] = projected_size
                    if row >= 0:
                        wx.CallAfter(self.list.SetStringItem, row, self.COL_EST, f"≈ {human_size(projected_size)}")

            seconds_to_convert = self.all_jobs_duration - overall
            try:
                remaining_time = format_time(seconds_to_convert / float(current_speed))
            except Exception:
                remaining_time = "?"

            wx.CallAfter(self.progress.SetValue, overall_progress)
            if gauge:
                wx.CallAfter(gauge.SetValue, row_progress)

            size_label = f" │ 💾 ≈ {human_size(projected_size)}" if projected_size else ""
            wx.CallAfter(
                self.progress_label.SetLabel,
                f"Очередь: {overall_progress}% │ Файл: {row_progress}% │ ⚡ {current_speed}x │ 🎞️ {current_fps} fps | ⏲ {remaining_time}{size_label}",
            )

        # cancel / skip
        if self.cancel_event.is_set() or self.skip_event.is_set():
            try:
                self.process.terminate()
                time.sleep(0.3)
            except Exception:
                pass

        rc = self.process.wait() if self.process else -1
        if self.cancel_event.is_set() or self.skip_event.is_set():
            return False

        if rc != 0:
            wx.CallAfter(self.log.AppendText, f"❌ FFmpeg завершился с кодом {rc}\n")
            return False

        return True

    # --- Skip ---
    def skip_rows(self, rows: list[int]):
        """Пропускает строки: текущую — прерывая ffmpeg, ожидающие — флагом для очереди."""
        if not self.converting:
            return
        for row in rows:
            widgets = self._widgets_at(row)
            if not widgets:
                continue
            status = self.list.GetItem(row, self.COL_STATUS).GetText()
            if self.row_order[row] == self._current_uid and "Конвертация" in status:
                self.log.AppendText(f"\n⏭ Пропуск текущего файла: {widgets.get('path')}\n")
                self.skip_event.set()
                if self.process and self.process.poll() is None:
                    try:
                        self.process.terminate()
                    except Exception:
                        pass
            elif status == "Ожидает" and row > self._queue_row:
                widgets["skip"] = True
                self.list.SetStringItem(row, self.COL_STATUS, "⏭ Пропущен")
                self.log.AppendText(f"⏭ Будет пропущен: {widgets.get('path')}\n")

    def unskip_rows(self, rows: list[int]):
        """Возвращает в очередь строки, помеченные на пропуск, до которых очередь ещё не дошла."""
        for row in rows:
            widgets = self._widgets_at(row)
            if widgets and widgets.get("skip") and row > self._queue_row:
                widgets["skip"] = False
                self.list.SetStringItem(row, self.COL_STATUS, "Ожидает")
                self.log.AppendText(f"↩ Возвращён в очередь: {widgets.get('path')}\n")

    def _remove_partial_output(self, output_file: str | None):
        """Удаляет недописанный выходной файл после пропуска (фоновый поток)."""
        if output_file and os.path.exists(output_file):
            try:
                os.remove(output_file)
                wx.CallAfter(self.log.AppendText, f"🗑 Удалён неполный файл: {os.path.basename(output_file)}\n")
            except Exception as e:
                wx.CallAfter(self.log.AppendText, f"⚠ Не удалось удалить {output_file}: {e}\n")

    # --- Cancel / close ---
    def cancel_conversion(self):
        self.cancel_event.set()
        if self.process and self.process.poll() is None:
            try:
                self.log.AppendText("\n⏹ Отмена конвертации...\n")
                self.process.terminate()
                time.sleep(0.5)
                try:
                    if self.process.poll() is None and sys.platform.startswith("win"):
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                except Exception:
                    pass
                self.log.AppendText("⏹ Остановлено.\n")
            except Exception as e:
                self.log.AppendText(f"⚠ Ошибка при завершении процесса: {e}\n")

        # удалить текущий неполный файл
        if self.current_output_file and os.path.exists(self.current_output_file):
            try:
                os.remove(self.current_output_file)
                self.log.AppendText(f"🗑 Удалён неполный файл: {os.path.basename(self.current_output_file)}\n")
            except Exception as e:
                self.log.AppendText(f"⚠ Не удалось удалить {self.current_output_file}: {e}\n")

        self.process = None
        self.converting = False
        wx.CallAfter(self.progress.SetValue, 0)
        wx.CallAfter(self.btn_start.SetLabel, "▶ Начать конвертацию")
        wx.CallAfter(self.progress_label.SetLabel, "⏹ Отменено пользователем")
