import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time

import wx

ctypes.windll.shcore.SetProcessDpiAwareness(2)

__VERSION__ = "0.1.1"


def get_resource_path(relative_path):
    """
    Определение пути для запуска из автономного exe файла.
    Pyinstaller cоздает временную папку, путь в _MEIPASS.
    """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


FFMPEG_PATH = get_resource_path("ffmpeg.exe")
FFPROBE_PATH = get_resource_path("ffprobe.exe")


# --- Определение битрейта по количеству каналов ---
def get_audio_bitrate(channels: int) -> str:
    if channels <= 1:
        return "128k"
    elif channels == 2:
        return "192k"
    elif channels <= 6:
        return "384k"
    elif channels >= 8:
        return "512k"
    return "256k"


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# --- Извлечение списка аудиодорожек ---
def get_audio_tracks(filepath):
    def fix_encoding(text: str):
        """Преобразование кодировки в UTF-8"""
        return text.encode("cp1251", "ignore").decode("utf-8", "ignore")

    try:
        result = subprocess.run(
            [
                FFPROBE_PATH,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index,codec_name,channels,bit_rate:stream_tags=language,title",
                "-of",
                "json",
                filepath,
            ],
            capture_output=True,
            text=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        data = json.loads(result.stdout)
        tracks = []

        for stream in data.get("streams", []):
            idx = stream.get("index", "?")
            codec = stream.get("codec_name", "?")
            ch = stream.get("channels", "?")
            br = stream.get("bit_rate")
            tags = stream.get("tags", {})

            lang = tags.get("language", "und")
            title_raw = tags.get("title", "").strip()
            title = fix_encoding(title_raw)

            if br:
                try:
                    br_kbps = int(int(br) / 1000)
                except Exception:
                    br_kbps = "?"
            else:
                br_kbps = "?"

            desc_parts = [f"{idx}: {codec}", f"{ch}ch", f"{br_kbps} kbps", lang]
            if title:
                desc_parts.append(f"«{title}»")

            desc = " (" + ", ".join(desc_parts[1:]) + ")"
            full_desc = f"{desc_parts[0]}{desc}"
            tracks.append(full_desc)

        return tracks

    except Exception as e:
        print("Ошибка при анализе аудио:", e)
        return []


def get_hdr_info(file_path: str) -> dict:
    """
    Определяет HDR тип видео: HDR10, HDR10+, HLG, Dolby Vision и т.п.
    Возвращает подробную структуру:
    {
        "is_hdr": True/False,
        "type": "HDR10 / Dolby Vision / SDR / HDR10+ / HLG",
        "requires_tonemap": True/False,
        "pix_fmt": "yuv420p10le",
        "color_transfer": "...",
        "color_primaries": "...",
        "color_space": "...",
        "dolby_profile": "5" (если найден)
    }
    """
    result = {
        "is_hdr": False,
        "type": "SDR",
        "requires_tonemap": False,
        "pix_fmt": "?",
        "color_transfer": "?",
        "color_primaries": "?",
        "color_space": "?",
        "dolby_profile": None,
    }

    try:
        # ffprobe JSON
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream_tags",
            file_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        data = json.loads(proc.stdout)

        if not data.get("streams"):
            return result

        stream = data["streams"][0]
        tags = stream.get("tags", {})

        color_primaries = stream.get("color_primaries", "")
        color_transfer = stream.get("color_transfer", "")
        color_space = stream.get("color_space", "")
        pix_fmt = stream.get("pix_fmt", "")

        result.update(
            {
                "color_primaries": color_primaries,
                "color_transfer": color_transfer,
                "color_space": color_space,
                "pix_fmt": pix_fmt,
            }
        )

        # --- Dolby Vision detection ---
        dv_profile = None
        for key, value in tags.items():
            if "dolby" in key.lower() or "dv" in key.lower():
                if "profile" in value.lower() or value.isdigit():
                    dv_profile = value
                    break
        if "dv_profile" in stream:
            dv_profile = stream["dv_profile"]

        if dv_profile:
            result["is_hdr"] = True
            result["type"] = f"Dolby Vision (P{dv_profile})"
            result["dolby_profile"] = dv_profile
            result["requires_tonemap"] = True
            return result

        # --- HDR10+ detection ---
        side_data = stream.get("side_data_list", [])
        if any("HDR10Plus" in str(d) for d in side_data):
            result["is_hdr"] = True
            result["type"] = "HDR10+"
            result["requires_tonemap"] = True
            return result

        # --- HDR10 / PQ / HLG detection ---
        if "smpte2084" in color_transfer.lower():
            result["is_hdr"] = True
            result["type"] = "HDR10 / PQ"
            result["requires_tonemap"] = True
        elif "arib-std-b67" in color_transfer.lower() or "hlg" in color_transfer.lower():
            result["is_hdr"] = True
            result["type"] = "HLG"
            result["requires_tonemap"] = False
        elif "bt2020" in color_primaries.lower():
            result["is_hdr"] = True
            result["type"] = "BT.2020 SDR"
            result["requires_tonemap"] = False
        else:
            result["is_hdr"] = False
            result["type"] = "SDR"
            result["requires_tonemap"] = False

    except Exception as e:
        print(f"⚠ Ошибка анализа HDR: {e}")

    return result


def get_video_info(filepath: str) -> dict:
    """
    Извлекает информацию о видеофайле (кодек, разрешение, FPS, битрейт, HDR и т.д.)
    через ffprobe и возвращает словарь с параметрами.
    """
    info = {
        "codec": "?",
        "width": "?",
        "height": "?",
        "fps": "?",
        "aspect": "?",
        "bitrate": "?",
        "hdr_type": "?",
        "duration": 0.0,
    }

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                (
                    "stream=codec_name,width,height,r_frame_rate,bit_rate,"
                    "display_aspect_ratio,color_space,color_transfer,color_primaries:"
                    "format=duration,bit_rate"
                ),
                "-of",
                "json",
                filepath,
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0] if data.get("streams") else {}
        fmt = data.get("format", {})

        info["codec"] = stream.get("codec_name", "?")
        info["width"] = stream.get("width", "?")
        info["height"] = stream.get("height", "?")

        # --- FPS ---
        fps_raw = stream.get("r_frame_rate", "0/0")
        try:
            num, den = fps_raw.split("/")
            info["fps"] = round(float(num) / float(den), 2) if float(den) != 0 else "?"
        except Exception:
            info["fps"] = "?"

        # --- Битрейт ---
        bitrate = stream.get("bit_rate") or fmt.get("bit_rate")
        if bitrate:
            try:
                info["bitrate"] = f"{int(bitrate) / 1_000_000:.2f} Мбит/с"
            except Exception:
                info["bitrate"] = "?"
        else:
            info["bitrate"] = "?"

        # --- Соотношение сторон ---
        info["aspect"] = stream.get("display_aspect_ratio", "?")

        # --- Длительность ---
        try:
            info["duration"] = float(fmt.get("duration", 0))
        except Exception:
            info["duration"] = 0.0

        # --- Определяем HDR тип ---
        color_trc = stream.get("color_transfer", "")
        if color_trc:
            if "smpte2084" in color_trc:
                info["hdr_type"] = "HDR10 / PQ"
            elif "arib-std-b67" in color_trc:
                info["hdr_type"] = "HLG"
            elif "bt2020" in color_trc:
                info["hdr_type"] = "BT.2020 SDR"
            else:
                info["hdr_type"] = color_trc
        else:
            info["hdr_type"] = "SDR"

        hdr_info = get_hdr_info(filepath)
        info["hdr_type"] = hdr_info["type"]
        info["requires_tonemap"] = hdr_info["requires_tonemap"]

    except Exception as e:
        print(f"⚠ Ошибка получения данных через ffprobe: {e}")

    return info


# --- Основное окно приложения ---
class VideoConverter(wx.Frame):
    def __init__(self):
        super().__init__(
            None,
            title=f"Video Converter (NVENC + AAC) {__VERSION__}",
            style=(wx.DEFAULT_FRAME_STYLE | wx.WANTS_CHARS) & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX),
        )
        panel = wx.Panel(self)
        panel.SetDropTarget(FileDropTarget(self))

        self.input_file = ""
        self.output_file = ""
        self.audio_tracks = []
        self.selected_track = 0
        self.qp_value = 22
        self.duration = 0
        self.log_visible = True
        self.converting = False
        self.process = None

        # --- Компоновка интерфейса ---
        vbox = wx.BoxSizer(wx.VERTICAL)

        # --- Ввод файла ---
        file_box = wx.BoxSizer(wx.HORIZONTAL)
        self.file_txt = wx.TextCtrl(panel, style=wx.TE_READONLY)
        self.btn_browse = wx.Button(panel, label="Выбрать файл...")
        file_box.Add(self.file_txt, 1, wx.ALL | wx.EXPAND, self.FromDIP(5))
        file_box.Add(self.btn_browse, 0, wx.ALL, self.FromDIP(5))
        vbox.Add(file_box, 0, wx.EXPAND)

        # --- Аудио дорожка ---
        self.audio_choice = wx.Choice(panel, choices=[])
        vbox.Add(wx.StaticText(panel, label="Аудио дорожка:"), 0, wx.LEFT | wx.TOP, self.FromDIP(8))
        vbox.Add(self.audio_choice, 0, wx.EXPAND | wx.ALL, self.FromDIP(5))

        # --- Режим кодирования (в одной строке) ---
        self.encode_mode = wx.RadioBox(
            panel,
            label="Режим кодирования",
            choices=["🎯 Постоянное качество (QP)", "📦 Постоянный битрейт (CBR)"],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS | wx.NO_BORDER,  # расположение в одну строку
        )
        self.encode_mode.SetSelection(0)
        self.encode_mode.Bind(wx.EVT_RADIOBOX, self.on_mode_change)
        vbox.Add(self.encode_mode, 0, wx.EXPAND | wx.ALL, self.FromDIP(5))

        # --- Слайдер качества / битрейта ---
        self.slider_label = wx.StaticText(panel, label="Качество (QP, меньше = лучше):")
        vbox.Add(self.slider_label, 0, wx.LEFT | wx.TOP, self.FromDIP(8))

        self.qp_slider = wx.Slider(panel, minValue=14, maxValue=30, value=22, style=wx.SL_HORIZONTAL)
        vbox.Add(self.qp_slider, 0, wx.EXPAND | wx.ALL, self.FromDIP(5))

        self.qp_label = wx.StaticText(panel, label="QP = 22")
        vbox.Add(self.qp_label, 0, wx.LEFT, self.FromDIP(12))

        self.qp_slider.Bind(wx.EVT_SLIDER, self.on_qp_change)

        # --- Дополнительные опции ---
        options_box = wx.BoxSizer(wx.HORIZONTAL)

        self.chk_limit_res = wx.CheckBox(panel, label="Ограничивать разрешение до FullHD (1920×1080)")
        self.chk_limit_res.SetValue(True)
        options_box.Add(self.chk_limit_res, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(20))

        # Тонмаппинг: авто / вкл / выкл
        self.tonemapping_label = wx.StaticText(panel, label="HDR→SDR:")
        options_box.Add(self.tonemapping_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(5))
        self.choice_tonemap = wx.Choice(panel, choices=["Авто", "Вкл", "Выкл"])
        self.choice_tonemap.SetSelection(0)  # Авто по умолчанию
        options_box.Add(self.choice_tonemap, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(20))

        # Чекбокс: не перекодировать видео
        self.chk_skip_video = wx.CheckBox(panel, label="Не перекодировать видео")
        self.chk_skip_video.SetValue(False)
        self.chk_skip_video.Bind(wx.EVT_CHECKBOX, self.on_skip_video)
        options_box.Add(self.chk_skip_video, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(20))

        # Чекбокс: debug
        self.chk_debug = wx.CheckBox(panel, label="Debug")
        self.chk_debug.SetValue(False)
        options_box.Add(self.chk_debug, 0, wx.ALIGN_CENTER_VERTICAL)

        vbox.Add(options_box, 0, wx.LEFT | wx.TOP | wx.RIGHT, self.FromDIP(10))

        # --- Кнопки управления ---
        btn_box = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start = wx.Button(panel, label="▶ Начать конвертацию")
        self.btn_toggle_log = wx.Button(panel, label="📋 Скрыть лог", size=self.FromDIP(wx.Size(100, 25)))
        self.btn_toggle_log.SetToolTip("Показать/Скрыть лог")
        btn_box.Add(self.btn_start, 1, wx.ALL | wx.EXPAND, self.FromDIP(5))
        btn_box.Add(self.btn_toggle_log, 0, wx.ALL, self.FromDIP(5))
        vbox.Add(btn_box, 0, wx.EXPAND)

        # --- Прогресс ---
        self.progress = wx.Gauge(panel, range=100, size=self.FromDIP(wx.Size(-1, 25)))
        vbox.Add(self.progress, 0, wx.EXPAND | wx.ALL, self.FromDIP(5))
        self.progress_label = wx.StaticText(panel, label="Прогресс: 0%")
        vbox.Add(self.progress_label, 0, wx.LEFT | wx.BOTTOM, self.FromDIP(5))

        # --- Лог ---
        self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        vbox.Add(self.log, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(5))

        panel.SetSizer(vbox)

        # --- Привязки событий ---
        self.btn_browse.Bind(wx.EVT_BUTTON, self.on_browse)
        self.btn_start.Bind(wx.EVT_BUTTON, self.on_convert)
        self.btn_toggle_log.Bind(wx.EVT_BUTTON, self.on_toggle_log)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.SetIcon(wx.Icon(get_resource_path("./images/favicon.png")))
        self.SetSize(self.FromDIP(wx.Size(750, 620)))
        self.Centre()
        self.on_toggle_log(None)
        self.Show()

        # --- Проверка наличия ffmpeg и ffprobe ---
        if not os.path.isfile(FFMPEG_PATH):
            self.log.AppendText("Не найден ffmpeg.exe\n")
            self.btn_start.Disable()
        if not os.path.isfile(FFPROBE_PATH):
            self.log.AppendText("Не найден ffprobe.exe\n")
            self.btn_browse.Disable()
            self.audio_choice.Disable()
            self.btn_start.Disable()

    def on_mode_change(self, event):
        """Переключает слайдер между режимами QP и CBR"""
        mode = self.encode_mode.GetSelection()
        if mode == 0:  # QP
            self.slider_label.SetLabel("Качество (QP, меньше = лучше):")
            self.qp_slider.SetRange(14, 30)
            self.qp_slider.SetValue(22)
            self.qp_label.SetLabel("QP = 22")
        else:  # CBR
            self.slider_label.SetLabel("Битрейт (Мбит/с):")
            self.qp_slider.SetRange(2, 25)  # битрейт от 2 до 25 Мбит/с
            self.qp_slider.SetValue(8)
            self.qp_label.SetLabel("Битрейт = 8.0 Мбит/с")

    # --- Показать/Скрыть лог ---
    def on_toggle_log(self, event):
        if self.log_visible:
            self.log.Hide()
            self.SetSize(self.FromDIP(wx.Size(750, 400)))
            self.btn_toggle_log.SetLabel("📋 Показать лог")
            self.Layout()
        else:
            self.log.Show()
            self.SetSize(self.FromDIP(wx.Size(750, 620)))
            self.btn_toggle_log.SetLabel("📋 Скрыть лог")
            self.Layout()
        self.log_visible = not self.log_visible

    # --- Обновление QP ---
    def on_qp_change(self, event):
        mode = self.encode_mode.GetSelection()
        val = self.qp_slider.GetValue()
        if mode == 0:
            self.qp_value = val
            self.qp_label.SetLabel(f"QP = {val}")
        else:
            self.bitrate_value = val
            self.qp_label.SetLabel(f"Битрейт = {val:.1f} Мбит/с")

    # --- Выбор файла ---
    def on_browse(self, event):
        with wx.FileDialog(
            self,
            "Выбери видеофайл",
            wildcard="Видео файлы (*.mkv;*.mp4;*.mov;*.avi)|*.mkv;*.mp4;*.mov;*.avi",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.set_input_file(dlg.GetPath())

    # --- Установка входного файла ---
    def set_input_file(self, path):
        self.input_file = path
        self.file_txt.SetValue(path)
        self.log.AppendText(f"{'-' * 30}\nВыбран файл: {path}\n")

        # --- Аудио дорожки ---
        tracks = get_audio_tracks(path)
        self.audio_tracks = tracks
        self.audio_choice.Set(tracks)
        if tracks:
            self.audio_choice.SetSelection(0)

        # --- Видеоинформация ---
        info = get_video_info(path)
        self.duration = info.get("duration", 0)
        duration_str = format_time(self.duration)

        self.log.AppendText(
            "🎥 Видео:\n"
            f"🔹Кодек: {info['codec']}\n"
            f"🔹Разрешение: {info['width']}×{info['height']}\n"
            f"🔹FPS: {info['fps']}\n"
            f"🔹Соотношение сторон: {info['aspect']}\n"
            f"🔹Битрейт: {info['bitrate']}\n"
            f"🔹Тип: {info['hdr_type']}\n"
            f"🔹Длительность: {duration_str} ({info['duration']:.1f} сек)\n"
        )

    # --- Конвертация ---
    def on_convert(self, event):
        if self.converting:
            self.cancel_conversion()
            return

        if not self.input_file:
            wx.MessageBox("Выберите файл!", "Ошибка", wx.OK | wx.ICON_ERROR)
            return

        self.selected_track = self.audio_choice.GetSelection()
        if self.selected_track == wx.NOT_FOUND:
            wx.MessageBox("Не выбрана аудиодорожка!", "Ошибка", wx.OK | wx.ICON_ERROR)
            return

        try:
            info = subprocess.run(
                [
                    FFPROBE_PATH,
                    "-v",
                    "error",
                    "-select_streams",
                    f"a:{self.selected_track}",
                    "-show_entries",
                    "stream=channels,codec_name",
                    "-of",
                    "json",
                    self.input_file,
                ],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.ch = json.loads(info.stdout).get("streams", [{}])[0].get("channels", 2)
            self.audiocodec = json.loads(info.stdout).get("streams", [{}])[0].get("codec_name", None)
        except Exception:
            self.ch = 2
            self.audiocodec = None

        bitrate = get_audio_bitrate(self.ch)
        self.output_file = os.path.splitext(self.input_file)[0] + "_conv.mp4"

        if os.path.exists(self.output_file):
            overwrite = wx.MessageBox(
                f"Файл {os.path.basename(self.output_file)} уже существует! Перезаписать?", "Внимание!", wx.YES_NO | wx.ICON_WARNING
            )
            if overwrite != wx.YES:
                return

        self.converting = True
        self.btn_start.SetLabel("⏹ Отмена")
        self.log.AppendText(f"\n🎬 Конвертация...\n")
        self.progress.SetValue(0)
        self.progress_label.SetLabel("Прогресс: 0%")

        self.disable_interface()
        threading.Thread(target=self.run_ffmpeg_with_progress, args=(bitrate,), daemon=True).start()

    # --- Основная конвертация ---
    def run_ffmpeg_with_progress(self, bitrate):
        audio_index = self.selected_track

        # Определяем, нужно ли кодировать аудио. Если кодек aac, то не кодируем
        if self.audiocodec == "aac":
            audio_codec_args = ["-c:a", "copy"]
            wx.CallAfter(self.log.AppendText, f"🎵 Перекодирование аудио: копирование (исходный кодек: {self.audiocodec})\n")
        else:
            audio_codec_args = ["-c:a", "aac", "-ac", str(self.ch), "-b:a", bitrate]
            wx.CallAfter(self.log.AppendText, f"🎵 Перекодирование аудио: {self.audiocodec}, каналов: {self.ch}, битрейт: {bitrate}\n")

        if not self.chk_skip_video.GetValue():  # если не нажато перекодировать видео
            # --- Проверка HDR / SDR ---
            hdr_info = get_hdr_info(self.input_file)
            hdr_type = hdr_info["type"]
            auto_tonemap = hdr_info["requires_tonemap"]

            tonemap_mode = self.choice_tonemap.GetSelection()  # 0=Авто, 1=Вкл, 2=Выкл
            if tonemap_mode == 2:
                needs_tonemap = False
            elif tonemap_mode == 1:
                needs_tonemap = True
            else:  # Авто
                needs_tonemap = auto_tonemap

            wx.CallAfter(
                self.log.AppendText, f"🎨Тип видео: {hdr_type} | Тонмаппинг: {'включён' if needs_tonemap else 'не требуется/выключён'}\n"
            )

            # --- Проверяем, нужно ли масштабировать ---
            scale_filter = ""
            if self.chk_limit_res.GetValue():  # если включено в GUI
                video_info = get_video_info(self.input_file)
                width = int(video_info.get("width") or 0)
                height = int(video_info.get("height") or 0)

                if width > 1920 or height > 1080:
                    # Вычисляем новое разрешение, сохраняя пропорции
                    aspect_ratio = width / height if height else 1
                    new_w, new_h = width, height

                    if width / 1920 >= height / 1080:
                        # Ограничение по ширине
                        new_w = 1920
                        new_h = int(1920 / aspect_ratio)
                    else:
                        # Ограничение по высоте
                        new_h = 1080
                        new_w = int(1080 * aspect_ratio)

                    # FFmpeg фильтр
                    scale_filter = ",scale='if(gt(iw,1920),1920,iw):if(gt(ih,1080),1080,ih):force_original_aspect_ratio=decrease'"

                    wx.CallAfter(self.log.AppendText, f"📐 Масштабирование: {width}×{height} → {new_w}×{new_h}\n")
                else:
                    wx.CallAfter(self.log.AppendText, f"📐 Масштабирование не требуется ({width}×{height})\n")
            else:
                wx.CallAfter(self.log.AppendText, "📐 Масштабирование: отключено пользователем\n")

            # --- Видео фильтр ---
            if needs_tonemap:
                print("needs_tonemap")
                vf_filter = (
                    "zscale=t=linear:npl=30,format=gbrpf32le,"
                    "zscale=p=bt709,tonemap=hable:param=1.5:desat=0,"
                    "zscale=t=bt709:m=bt709:r=pc,format=yuv420p"
                    f"{scale_filter}"
                )
            else:
                vf_filter = f"format=yuv420p{scale_filter}"

            # --- Определяем режим кодирования ---
            mode = self.encode_mode.GetSelection()
            if mode == 0:  # Постоянное качество
                video_codec_args = ["-qp", str(self.qp_value), "-b:v", "0"]
                wx.CallAfter(self.log.AppendText, f"🎯 Режим: постоянное качество (QP={self.qp_value})\n")
            else:  # Постоянный битрейт
                target_bitrate = f"{int(self.qp_slider.GetValue() * 1000)}k"
                video_codec_args = ["-b:v", target_bitrate, "-maxrate", target_bitrate, "-bufsize", "2M"]
                wx.CallAfter(self.log.AppendText, f"📦 Режим: постоянный битрейт ({target_bitrate})\n")

            # --- Команда FFmpeg ---
            cmd = [
                FFMPEG_PATH,
                "-hide_banner",
                "-y",
                "-i",
                self.input_file,
                "-map",
                "0:v:0",
                "-map",
                f"0:a:{audio_index}",
                "-c:v",
                "h264_nvenc",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                vf_filter,
                "-preset",
                "p4",
                *video_codec_args,
                "-profile:v",
                "high",
                "-tune",
                "hq",
                "-spatial_aq",
                "1",
                "-temporal_aq",
                "1",
                *audio_codec_args,
                "-map_metadata",
                "-1",
                "-sn",
                # "-movflags",
                # "+faststart",
                self.output_file,
            ]
        else:
            wx.CallAfter(self.log.AppendText, "🎥 Перекодирование видео: отключено пользователем\n")
            # не конвертировать видео
            cmd = [
                FFMPEG_PATH,
                "-hide_banner",
                "-y",
                "-i",
                self.input_file,
                "-map",
                "0:v:0",
                "-map",
                f"0:a:{audio_index}",
                "-c:v",
                "copy",
                *audio_codec_args,
                "-map_metadata",
                "-1",
                "-sn",
                # "-movflags",
                # "+faststart",
                self.output_file,
            ]

        self.process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        total_duration = self.duration or 1
        time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        time_elapsed_regex = re.compile(r"elapsed=(\d+):(\d+):(\d+)\.\d+")
        speed_regex = re.compile(r"speed=\s*([\d\.]+)x")
        fps_regex = re.compile(r"fps=\s*([\d\.]+)")

        current_speed = "?"
        current_fps = "?"

        for line in self.process.stderr:
            # показываем каждую строку из stderr в логе
            if self.chk_debug.GetValue():
                autoscroll = self.log.HasFocus()
                if autoscroll:
                    wx.CallAfter(self.log.AppendText, line)
                else:
                    self.log.GetParent().Freeze()
                    wx.CallAfter(self.log.AppendText, line)
                    self.log.GetParent().Thaw()

            if self.process.poll() is not None:
                break
            match = time_regex.search(line)
            if match:
                h, m, s = match.groups()
                current_time = int(h) * 3600 + int(m) * 60 + float(s)
                progress = min(int(current_time / total_duration * 100), 100)

                # Парсим скорость
                speed_match = speed_regex.search(line)
                if speed_match:
                    current_speed = speed_match.group(1) + "x"

                # Парсим FPS
                fps_match = fps_regex.search(line)
                if fps_match:
                    current_fps = fps_match.group(1)

                elapsed_time_match = time_elapsed_regex.search(line)
                if elapsed_time_match:
                    elapsed_time = elapsed_time_match.group(1) + ":" + elapsed_time_match.group(2) + ":" + elapsed_time_match.group(3)

                # Обновляем GUI
                wx.CallAfter(self.progress.SetValue, progress)
                wx.CallAfter(
                    self.progress_label.SetLabel,
                    f"Прогресс: {progress}% │ ⚡ {current_speed} │ 🎞️ {current_fps} fps │ ⏱ {elapsed_time}",
                )

        if self.process and self.process.poll() is None:
            self.process.wait()

        wx.CallAfter(self.progress.SetValue, 100)
        wx.CallAfter(self.btn_start.SetLabel, "▶ Начать конвертацию")
        wx.CallAfter(self.progress_label.SetLabel, "✅ Завершено │ ⚡ 1.0x │ 🎞️ — fps")
        wx.CallAfter(self.log.AppendText, f"\nРабота завершена: {self.output_file}\n")
        self.converting = False
        self.process = None
        self.enable_interface()

    # --- Отмена конвертации ---
    def cancel_conversion(self):
        if self.process and self.process.poll() is None:
            try:
                self.log.AppendText("\n⏹ Отмена конвертации...\n")
                self.process.terminate()
                time.sleep(0.5)
                if self.process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                self.log.AppendText("❌ Конвертация отменена пользователем.\n")
            except Exception as e:
                self.log.AppendText(f"Ошибка при завершении процесса: {e}\n")

        # Удаляем неполный файл
        if self.output_file and os.path.exists(self.output_file):
            try:
                os.remove(self.output_file)
                self.log.AppendText(f"🗑 Удалён неполный файл: {os.path.basename(self.output_file)}\n")
            except Exception as e:
                self.log.AppendText(f"⚠️ Не удалось удалить {self.output_file}: {e}\n")

        self.process = None
        self.converting = False
        wx.CallAfter(self.btn_start.SetLabel, "▶ Начать конвертацию")
        wx.CallAfter(self.progress_label.SetLabel, "⏹ Отменено пользователем")

    # --- Закрытие окна ---
    def on_close(self, event):
        if self.converting:
            res = wx.MessageBox(
                "Конвертация ещё выполняется. Остановить и выйти?",
                "Подтверждение",
                wx.YES_NO | wx.ICON_WARNING,
            )
            if res != wx.YES:
                event.Veto()
                return
            else:
                self.cancel_conversion()
        self.Destroy()

    # --- Отключить интерфейс ---
    def disable_interface(self):
        self.btn_browse.Disable()
        self.qp_slider.Disable()
        self.audio_choice.Disable()

    # --- Включить интерфейс ---
    def enable_interface(self):
        self.btn_browse.Enable()
        self.qp_slider.Enable()
        self.audio_choice.Enable()

    # --- Блокировка интерфейса ---
    def on_skip_video(self, event):
        if self.chk_skip_video.GetValue():
            self.chk_limit_res.Disable()
            self.tonemapping_label.Disable()
            self.choice_tonemap.Disable()
            self.slider_label.Disable()
            self.qp_slider.Disable()
            self.encode_mode.Disable()
        else:
            self.chk_limit_res.Enable()
            self.tonemapping_label.Enable()
            self.choice_tonemap.Enable()
            self.slider_label.Enable()
            self.qp_slider.Enable()
            self.encode_mode.Enable()
        self.Layout()


# --- Drag&Drop класс ---
class FileDropTarget(wx.FileDropTarget):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame

    def OnDropFiles(self, x, y, filenames):
        if filenames:
            self.frame.set_input_file(filenames[0])
        return True


# --- Запуск ---
if __name__ == "__main__":
    app = wx.App(False)
    top = VideoConverter()
    app.MainLoop()
