"""Контрольная кодировка: оценка размера по нескольким фрагментам файла."""
import os
import subprocess
import tempfile
import threading

import wx

from vc.estimate import PROBE_POSITIONS, PROBE_SEGMENT_SEC, estimate_qp_video_bitrate_bps
from vc.media_probe import run_ffprobe_json, streams_of_type
from vc.resources import FFMPEG_PATH, FFPROBE_PATH
from vc.settings import RowSettings
from vc.utils import to_int


class ProbeMixin:
    """
    Примесь к VideoConverter. Фрагменты кодируются теми же аргументами ffmpeg,
    что и при конвертации (self._build_video_args), а измеренный битрейт
    подставляется в прогноз размера.
    """
    def start_probe(self, rows: list[int]):
        """
        Запускает контрольную кодировку для строк: несколько фрагментов файла
        кодируются теми же аргументами ffmpeg, что и при конвертации, а
        измеренный битрейт видео подставляется в прогноз размера.
        """
        if self.converting or self.probing:
            return

        global_settings = self._global_settings_for_estimate()
        jobs = []
        for row in rows:
            widgets = self._widgets_at(row)
            if not widgets:
                continue
            status = self.list.GetItem(row, self.COL_STATUS).GetText()
            if "Конвертация" in status or "Готово" in status:
                continue
            settings: RowSettings = widgets.get("settings") or RowSettings()
            if settings.is_global:
                settings = global_settings
            if settings.skip_video or settings.encode_mode != 0:
                # Для копирования потока и CBR прогноз и так точный.
                continue
            jobs.append((row, widgets, settings, status))

        if not jobs:
            self.log.AppendText("\n⚠ Нет строк для контрольной кодировки: нужны файлы в режиме QP, ещё не сконвертированные.\n")
            return

        self.probing = True
        self.probe_cancel.clear()
        self.btn_start.SetLabel("⏹ Отменить оценку")
        self.progress.SetValue(0)
        self.log.AppendText(f"\n{'-' * 30}\n🎯 Контрольная кодировка: файлов — {len(jobs)}\n")
        self.disable_interface()
        threading.Thread(target=self.probe_worker, args=(jobs,), daemon=True).start()

    def probe_worker(self, jobs: list[tuple]):
        segments_total = len(jobs) * len(PROBE_POSITIONS)
        done = 0
        try:
            for job_idx, (row, widgets, settings, status) in enumerate(jobs, start=1):
                if self.probe_cancel.is_set():
                    break
                path = widgets.get("path")
                gauge: wx.Gauge | None = widgets.get("gauge")
                duration = float(widgets.get("duration") or 0.0)
                if not path or not os.path.isfile(path) or duration <= 0:
                    done += len(PROBE_POSITIONS)
                    continue

                wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "🎯 Оценка...")
                if gauge:
                    wx.CallAfter(gauge.SetValue, 0)
                wx.CallAfter(self.log.AppendText, f"\n🎯 [{job_idx}/{len(jobs)}] {os.path.basename(path)}\n")

                video_args = self._build_video_args(
                    input_path=path,
                    video_info=widgets.get("info") or {},
                    skip_video=False,
                    encode_mode=0,
                    qp_slider=settings.quality,
                    limit_res=settings.limit_res,
                    tonemap_mode=settings.tonemapping,
                )

                # Короткий файл кодируем целиком одним фрагментом.
                if duration <= PROBE_SEGMENT_SEC * len(PROBE_POSITIONS) * 1.5:
                    segments = [(0.0, duration)]
                else:
                    segments = [(duration * pos, float(PROBE_SEGMENT_SEC)) for pos in PROBE_POSITIONS]

                total_bytes = 0
                total_sec = 0.0
                with tempfile.TemporaryDirectory(prefix="vc_probe_") as tmp:
                    out = os.path.join(tmp, "probe.mp4")
                    for seg_idx, (start, length) in enumerate(segments, start=1):
                        if self.probe_cancel.is_set():
                            break
                        wx.CallAfter(
                            self.progress_label.SetLabel,
                            f"Оценка: файл {job_idx} из {len(jobs)} │ фрагмент {seg_idx}/{len(segments)}",
                        )
                        seg_bytes, seg_sec = self._probe_encode_segment(path, start, length, video_args, out)
                        total_bytes += seg_bytes
                        total_sec += seg_sec
                        done += 1
                        if gauge:
                            wx.CallAfter(gauge.SetValue, int(seg_idx / len(segments) * 100))
                        wx.CallAfter(self.progress.SetValue, int(done / segments_total * 100))
                    # Если файл короткий, один фрагмент учитывается как все.
                    done += len(PROBE_POSITIONS) - len(segments)

                wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, status)
                if gauge:
                    wx.CallAfter(gauge.SetValue, 0)
                if self.probe_cancel.is_set() or total_sec <= 0:
                    continue

                video_bps = int(total_bytes * 8 / total_sec)
                model_bps = estimate_qp_video_bitrate_bps(
                    widgets.get("info") or {}, settings.quality, settings.limit_res, self.nvenc_available
                )
                widgets["probe"] = {
                    "video_bps": video_bps,
                    "qp": settings.quality,
                    "limit_res": settings.limit_res,
                    "tonemapping": settings.tonemapping,
                    "nvenc": self.nvenc_available,
                }
                wx.CallAfter(self.update_row_estimate, row)
                wx.CallAfter(
                    self.log.AppendText,
                    f"   📊 Видео: {video_bps / 1e6:.2f} Мбит/с при QP={settings.quality} "
                    f"(модель давала {model_bps / 1e6:.2f} Мбит/с)\n",
                )

            if self.probe_cancel.is_set():
                wx.CallAfter(self.progress_label.SetLabel, "⏹ Оценка отменена")
            else:
                wx.CallAfter(self.progress_label.SetLabel, "✅ Оценка завершена")
                wx.CallAfter(self.log.AppendText, "✅ Контрольная кодировка завершена\n")
        finally:
            self.probing = False
            self.probe_process = None
            wx.CallAfter(self.btn_start.SetLabel, "▶ Начать конвертацию")
            wx.CallAfter(self.progress.SetValue, 0)
            wx.CallAfter(self.enable_interface)

    def _probe_encode_segment(self, path: str, start: float, length: float, video_args: list[str], out: str) -> tuple[int, float]:
        """Кодирует фрагмент без звука; возвращает (байты видео, секунды)."""
        cmd = [
            FFMPEG_PATH,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            path,
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            *video_args,
            out,
        ]
        try:
            self.probe_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            _, err = self.probe_process.communicate()
            rc = self.probe_process.returncode
        except Exception as e:
            wx.CallAfter(self.log.AppendText, f"   ❌ Не удалось запустить ffmpeg: {e}\n")
            return 0, 0.0
        if self.probe_cancel.is_set():
            return 0, 0.0
        if rc != 0:
            wx.CallAfter(self.log.AppendText, f"   ❌ FFmpeg завершился с кодом {rc}: {(err or '').strip()[-300:]}\n")
            return 0, 0.0

        probe = run_ffprobe_json(
            [FFPROBE_PATH, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=bit_rate:format=duration", "-of", "json", out]
        )
        videos = streams_of_type(probe, "video")
        seg_sec = float((probe.get("format") or {}).get("duration") or 0.0)
        bit_rate = to_int(videos[0].get("bit_rate")) if videos else 0
        if seg_sec <= 0:
            return 0, 0.0
        if bit_rate > 0:
            return int(bit_rate * seg_sec / 8), seg_sec
        try:
            return os.path.getsize(out), seg_sec
        except OSError:
            return 0, 0.0

    def cancel_probe(self):
        self.probe_cancel.set()
        proc = self.probe_process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self.log.AppendText("\n⏹ Отмена контрольной кодировки...\n")
