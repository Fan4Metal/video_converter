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


# --- Основное окно приложения ---
class VideoConverter(wx.Frame):
    def __init__(self):
        super().__init__(
            None,
            title="🎬 Video Converter (NVENC + AAC)",
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
        file_box.Add(self.file_txt, 1, wx.ALL | wx.EXPAND, 5)
        file_box.Add(self.btn_browse, 0, wx.ALL, 5)
        vbox.Add(file_box, 0, wx.EXPAND)

        # --- Аудио дорожка ---
        self.audio_choice = wx.Choice(panel, choices=[])
        vbox.Add(wx.StaticText(panel, label="Аудио дорожка:"), 0, wx.LEFT | wx.TOP, 8)
        vbox.Add(self.audio_choice, 0, wx.EXPAND | wx.ALL, 5)

        # --- Слайдер качества ---
        vbox.Add(wx.StaticText(panel, label="Качество (QP, меньше = лучше):"), 0, wx.LEFT | wx.TOP, 8)
        self.qp_slider = wx.Slider(panel, minValue=14, maxValue=30, value=22, style=wx.SL_HORIZONTAL)
        vbox.Add(self.qp_slider, 0, wx.EXPAND | wx.ALL, 5)
        self.qp_label = wx.StaticText(panel, label="QP = 22")
        vbox.Add(self.qp_label, 0, wx.LEFT, 12)
        self.qp_slider.Bind(wx.EVT_SLIDER, self.on_qp_change)

        # --- Дополнительные опции ---
        options_box = wx.BoxSizer(wx.HORIZONTAL)

        self.chk_limit_res = wx.CheckBox(panel, label="Ограничивать разрешение до FullHD (1920×1080)")
        self.chk_limit_res.SetValue(True)

        self.chk_debug = wx.CheckBox(panel, label="Debug (показывать вывод ffmpeg)")
        self.chk_debug.SetValue(False)

        options_box.Add(self.chk_limit_res, 1, wx.RIGHT, 20)
        options_box.Add(self.chk_debug, 0)

        vbox.Add(options_box, 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)

        # --- Кнопки управления ---
        btn_box = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start = wx.Button(panel, label="▶ Начать конвертацию")
        self.btn_toggle_log = wx.Button(panel, label="📋 Скрыть лог", size=self.FromDIP(wx.Size(100, 25)))
        self.btn_toggle_log.SetToolTip("Показать/Скрыть лог")
        btn_box.Add(self.btn_start, 1, wx.ALL | wx.EXPAND, 5)
        btn_box.Add(self.btn_toggle_log, 0, wx.ALL, 5)
        vbox.Add(btn_box, 0, wx.EXPAND)

        # --- Прогресс ---
        self.progress = wx.Gauge(panel, range=100, size=(-1, 25))
        vbox.Add(self.progress, 0, wx.EXPAND | wx.ALL, 5)
        self.progress_label = wx.StaticText(panel, label="Прогресс: 0%")
        vbox.Add(self.progress_label, 0, wx.LEFT, 12)

        # --- Лог ---
        self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        vbox.Add(self.log, 1, wx.EXPAND | wx.ALL, 5)

        panel.SetSizer(vbox)

        # --- Привязки событий ---
        self.btn_browse.Bind(wx.EVT_BUTTON, self.on_browse)
        self.btn_start.Bind(wx.EVT_BUTTON, self.on_convert)
        self.btn_toggle_log.Bind(wx.EVT_BUTTON, self.on_toggle_log)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.SetSize(self.FromDIP(wx.Size(750, 580)))
        self.SetMinSize(self.FromDIP(wx.Size(750, 269)))
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

    # --- Показать/Скрыть лог ---
    def on_toggle_log(self, event):
        if self.log_visible:
            self.log.Hide()
            self.Layout()
            self.btn_toggle_log.SetLabel("📋 Показать лог")
            self.SetSize(self.FromDIP(wx.Size(750, 275)))
        else:
            self.log.Show()
            self.Layout()
            self.btn_toggle_log.SetLabel("📋 Скрыть лог")
            self.SetSize(self.FromDIP(wx.Size(750, 580)))
        self.log_visible = not self.log_visible

    # --- Обновление QP ---
    def on_qp_change(self, event):
        self.qp_value = self.qp_slider.GetValue()
        self.qp_label.SetLabel(f"QP = {self.qp_value}")

    # --- Выбор файла ---
    def on_browse(self, event):
        with wx.FileDialog(
            self, "Выбери видеофайл", wildcard="Видео файлы (*.mkv;*.mp4;*.mov)|*.mkv;*.mp4;*.mov", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.set_input_file(dlg.GetPath())

    # --- Установка входного файла ---
    def set_input_file(self, path):
        self.input_file = path
        self.file_txt.SetValue(path)
        self.log.AppendText(f"Выбран файл: {path}\n")

        tracks = get_audio_tracks(path)
        self.audio_tracks = tracks
        self.audio_choice.Set(tracks)
        if tracks:
            self.audio_choice.SetSelection(0)

        try:
            dur = subprocess.run(
                [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True
            )
            self.duration = float(dur.stdout.strip())
            self.log.AppendText(f"Длительность: {self.duration:.1f} сек\n")
        except Exception:
            self.duration = 0

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
                    "stream=channels",
                    "-of",
                    "csv=p=0",
                    self.input_file,
                ],
                capture_output=True,
                text=True,
            )
            ch = int(info.stdout.strip()) if info.stdout.strip() else 2
        except Exception:
            ch = 2

        bitrate = get_audio_bitrate(ch)
        self.output_file = os.path.splitext(self.input_file)[0] + "_conv.mp4"

        if os.path.exists(self.output_file):
            overwrite = wx.MessageBox(
                f"Файл {os.path.basename(self.output_file)} уже существует! Перезаписать?", "Внимание!", wx.YES_NO | wx.ICON_WARNING
            )
            if overwrite != wx.YES:
                return

        self.converting = True
        self.btn_start.SetLabel("⏹ Отмена")
        self.log.AppendText(f"\n🎬 Конвертация...\nКаналов: {ch} → {bitrate}, QP: {self.qp_value}\n")
        self.progress.SetValue(0)
        self.progress_label.SetLabel("Прогресс: 0%")

        self.disable_interface()
        threading.Thread(target=self.run_ffmpeg_with_progress, args=(bitrate,), daemon=True).start()

    # --- Основная конвертация ---
    def run_ffmpeg_with_progress(self, bitrate):
        audio_index = self.selected_track
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
            "-preset",
            "p4",
            "-qp",
            str(self.qp_value),
            "-b:v",
            "0",
            "-profile:v",
            "high",
            "-tune",
            "hq",
            "-spatial_aq",
            "1",
            "-temporal_aq",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            bitrate,
            "-movflags",
            "+faststart",
            self.output_file,
        ]

        self.process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, universal_newlines=True)

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

    def disable_interface(self):
        print("disable_interface")
        self.btn_browse.Disable()
        self.qp_slider.Disable()
        self.audio_choice.Disable()

    def enable_interface(self):
        print("enable_interface")
        self.btn_browse.Enable()
        self.qp_slider.Enable()
        self.audio_choice.Enable()


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
