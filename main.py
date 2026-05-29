import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import winreg
import winsound
from collections import Counter

import wx
from mutagen.mp4 import MP4, MP4StreamInfoError
from wx.adv import AboutDialogInfo
from wx.lib.agw import ultimatelistctrl as ULC

# --- HiDPI (Windows only) ---
if sys.platform.startswith("win"):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        pass

__VERSION__ = "0.3.0"


def get_resource_path(relative_path: str) -> str:
    """
    PyInstaller создает временную папку, путь в sys._MEIPASS.
    В обычном запуске берем текущую папку.
    """
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


FFMPEG_PATH = get_resource_path("ffmpeg.exe")
FFPROBE_PATH = get_resource_path("ffprobe.exe")
MPV_PATH = get_resource_path("mpv.exe")


def get_ffmpeg_version(ffmpeg_path: str) -> dict:
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = result.stdout

        if not output:
            output = result.stderr

        ffmpeg_version_match = re.search(r"ffmpeg version ([\w.-]+)", output)
        ffmpeg_version = ffmpeg_version_match.group(1) if ffmpeg_version_match else "Unknown"

        libavcodec_match = re.search(r"libavcodec\s+(\d+\.\s*\d+\.\s*\d+)", output)
        libavcodec_version = libavcodec_match.group(1).replace(" ", "") if libavcodec_match else "Unknown"

        return {"ffmpeg": ffmpeg_version, "libavcodec": libavcodec_version}
    except FileNotFoundError:
        return "FFmpeg не установлен"


def save_reg(name: str, data: str):
    """
    Сохраняет в реестре параметры приложения.
    "save_path" - путь к папке для сохранения файлов.
    """

    soft = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, "SOFTWARE")
    key = winreg.CreateKey(soft, "video_converter")
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, data)
    if key:
        winreg.CloseKey(key)


def get_reg(name):
    reg_path = R"SOFTWARE\video_converter"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ)
        value = winreg.QueryValueEx(key, name)[0]
        winreg.CloseKey(key)
        return value
    except WindowsError:
        return


def copy_mp4_tags(source_path: str, dest_path: str):
    try:
        video = MP4(source_path)
        new_video = MP4(dest_path)
        for tag in video.tags:
            new_video.tags[tag] = video.tags[tag]
        new_video.save()
        return True
    except MP4StreamInfoError as e:
        print(e)
    except Exception as e:
        print(e)


def read_from_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def human_size(num_bytes: int) -> str:
    try:
        num = float(num_bytes)
    except Exception:
        return "?"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024.0:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


# --- Определение битрейта по количеству каналов ---
def get_audio_bitrate(channels: int) -> str:
    if channels <= 1:
        return "128k"
    if channels == 2:
        return "192k"
    if channels <= 6:
        return "384k"
    if channels >= 8:
        return "512k"
    return "256k"


def run_ffprobe_json(args: list[str]) -> dict:
    """
    Унифицированный вызов ffprobe, возвращает JSON dict (или {}).
    Консоль НЕ скрываем.
    """
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if not p.stdout.strip():
            return {}
        return json.loads(p.stdout)
    except Exception:
        return {}


def get_audio_tracks(filepath: str) -> list[str]:
    """
    Возвращает список строк для Choice.
    Важно: stream.index у ffprobe — это индекс потока в контейнере (может быть 1,2,3...),
    а выбор у пользователя будет 0..N-1 (порядок аудио-стримов).
    Мы показываем stream.index в тексте, но мапим по порядку (a:0, a:1...).
    """

    def fix_encoding(text: str) -> str:
        try:
            repaired = text.encode("cp1251").decode("utf-8")
        except Exception:
            return text
        if any(marker in text for marker in ("Ð", "Ñ", "Â", "Ã")) and repaired:
            return repaired
        return text

    data = run_ffprobe_json([
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
    ])

    tracks: list[str] = []
    for stream in data.get("streams", []):
        idx = stream.get("index", "?")
        codec = stream.get("codec_name", "?")
        ch = stream.get("channels", "?")
        br = stream.get("bit_rate")
        tags = stream.get("tags", {}) or {}

        lang = tags.get("language", "und")
        title_raw = (tags.get("title") or "").strip()
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
        tracks.append(f"{desc_parts[0]}{desc}")

    return tracks


def get_subtitle_tracks(filepath: str) -> list[dict]:
    """
    Возвращает субтитры в порядке s:0, s:1...
    Для MP4 сохраняем только текстовые дорожки, которые ffmpeg умеет
    перекодировать в mov_text.
    """

    def fix_encoding(text: str) -> str:
        try:
            repaired = text.encode("cp1251").decode("utf-8")
        except Exception:
            return text
        if any(marker in text for marker in ("Ð", "Ñ", "Â", "Ã")) and repaired:
            return repaired
        return text

    data = run_ffprobe_json([
        FFPROBE_PATH,
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title",
        "-of",
        "json",
        filepath,
    ])

    text_codecs = {
        "subrip",
        "ass",
        "ssa",
        "webvtt",
        "mov_text",
        "text",
    }

    tracks: list[dict] = []
    for subtitle_order, stream in enumerate(data.get("streams", [])):
        idx = stream.get("index", "?")
        codec = stream.get("codec_name", "?")
        tags = stream.get("tags", {}) or {}
        lang = tags.get("language", "und")
        title_raw = (tags.get("title") or "").strip()
        title = fix_encoding(title_raw)
        supported = str(codec).lower() in text_codecs

        desc_parts = [f"{idx}: {codec}", lang]
        if title:
            desc_parts.append(f"«{title}»")
        if not supported:
            desc_parts.append("не для MP4")

        tracks.append({
            "order": subtitle_order,
            "codec": codec,
            "language": lang,
            "title": title,
            "supported": supported,
            "display": f"{desc_parts[0]} (" + ", ".join(desc_parts[1:]) + ")",
        })

    return tracks


def get_audio_channels(input_file: str, selected_track: int) -> int:
    """
    selected_track — это порядковый номер аудио-стрима среди аудио (a:0, a:1...),
    то есть именно то, что Choice.GetSelection() возвращает.
    """
    data = run_ffprobe_json([
        FFPROBE_PATH,
        "-v",
        "error",
        "-select_streams",
        f"a:{selected_track}",
        "-show_entries",
        "stream=channels",
        "-of",
        "json",
        input_file,
    ])
    try:
        return int((data.get("streams") or [{}])[0].get("channels") or 2)
    except Exception:
        return 2


def get_hdr_info(file_path: str) -> dict:
    """
    Упрощённый HDR анализ.
    """
    result = {
        "is_hdr": False,
        "type": "SDR",
        "requires_tonemap": False,
        "pix_fmt": "?",
        "color_transfer": "",
        "color_primaries": "",
        "color_space": "",
        "dolby_profile": None,
    }

    data = run_ffprobe_json([
        FFPROBE_PATH,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=pix_fmt,color_transfer,color_primaries,color_space:stream_tags:stream=side_data_list",
        "-of",
        "json",
        file_path,
    ])

    streams = data.get("streams") or []
    if not streams:
        return result

    stream = streams[0]
    tags = stream.get("tags", {}) or {}

    color_primaries = (stream.get("color_primaries") or "").lower()
    color_transfer = (stream.get("color_transfer") or "").lower()
    color_space = (stream.get("color_space") or "").lower()
    pix_fmt = stream.get("pix_fmt") or "?"

    result.update({
        "pix_fmt": pix_fmt,
        "color_transfer": color_transfer,
        "color_primaries": color_primaries,
        "color_space": color_space,
    })

    # Dolby Vision (очень приблизительно)
    dv_profile = None
    for k, v in tags.items():
        ks = str(k).lower()
        vs = str(v).lower()
        if "dolby" in ks or "dv" in ks:
            if "profile" in vs or vs.isdigit():
                dv_profile = v
                break

    if dv_profile:
        result["is_hdr"] = True
        result["type"] = f"Dolby Vision (P{dv_profile})"
        result["dolby_profile"] = dv_profile
        result["requires_tonemap"] = True
        return result

    side_data = stream.get("side_data_list", []) or []
    if any("hdr10plus" in str(d).lower() for d in side_data):
        result["is_hdr"] = True
        result["type"] = "HDR10+"
        result["requires_tonemap"] = True
        return result

    if "smpte2084" in color_transfer:
        result["is_hdr"] = True
        result["type"] = "HDR10 / PQ"
        result["requires_tonemap"] = True
    elif "arib-std-b67" in color_transfer or "hlg" in color_transfer:
        result["is_hdr"] = True
        result["type"] = "HLG"
        result["requires_tonemap"] = False
    elif "bt2020" in color_primaries:
        result["is_hdr"] = True
        result["type"] = "BT.2020 SDR"
        result["requires_tonemap"] = False
    else:
        result["is_hdr"] = False
        result["type"] = "SDR"
        result["requires_tonemap"] = False

    return result


def get_video_info(filepath: str) -> dict:
    info = {
        "codec": "?",
        "width": "?",
        "height": "?",
        "fps": "?",
        "aspect": "?",
        "bitrate": "?",
        "hdr_type": "SDR",
        "requires_tonemap": False,
        "duration": 0.0,
        "size": 0,
    }

    data = run_ffprobe_json([
        FFPROBE_PATH,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        (
            "stream=codec_name,width,height,r_frame_rate,bit_rate,display_aspect_ratio,"
            "color_transfer,color_primaries,color_space:format=duration,bit_rate,size"
        ),
        "-of",
        "json",
        filepath,
    ])

    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}

    info["codec"] = stream.get("codec_name", "?")
    info["width"] = stream.get("width", "?")
    info["height"] = stream.get("height", "?")
    info["aspect"] = stream.get("display_aspect_ratio", "?")
    info["size"] = int(fmt.get("size") or 0)

    # FPS
    fps_raw = stream.get("r_frame_rate", "0/0")
    try:
        num, den = fps_raw.split("/")
        info["fps"] = round(float(num) / float(den), 2) if float(den) != 0 else "?"
    except Exception:
        info["fps"] = "?"

    # bitrate
    br = stream.get("bit_rate") or fmt.get("bit_rate")
    if br:
        try:
            info["bitrate"] = f"{int(br) / 1_000_000:.2f} Мбит/с"
        except Exception:
            info["bitrate"] = "?"
    else:
        info["bitrate"] = "?"

    # duration
    try:
        info["duration"] = float(fmt.get("duration") or 0.0)
    except Exception:
        info["duration"] = 0.0

    hdr = get_hdr_info(filepath)
    info["hdr_type"] = hdr["type"]
    info["requires_tonemap"] = bool(hdr["requires_tonemap"])

    return info


def unique_output_path(save_folder: str, input_path: str, add_conv_suffix: bool = True, output_ext: str = ".mp4") -> str:
    """
    Возвращает уникальный путь для выходного файла.

    :param save_folder: Папка для сохранения. Если не существует или пустая,
                        файл создаётся рядом с input_path.
    :param input_path: Путь к исходному файлу.
    :param add_conv_suffix: Добавлять ли суффикс "_conv" к имени файла.
    :param output_ext: Расширение выходного файла, по умолчанию ".mp4".
    :return: Уникальный путь к выходному файлу.
    """
    input_dir = os.path.dirname(input_path)
    input_name = os.path.splitext(os.path.basename(input_path))[0]

    target_dir = save_folder if save_folder and os.path.isdir(save_folder) else input_dir

    base_name = f"{input_name}_conv" if add_conv_suffix else input_name
    out_path = os.path.join(target_dir, f"{base_name}{output_ext}")

    if not os.path.exists(out_path):
        return out_path

    n = 2
    while True:
        candidate = os.path.join(target_dir, f"{base_name}_{n}{output_ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


# --- Drag&Drop класс ---
class FileDropTarget(wx.FileDropTarget):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame

    def OnDropFiles(self, x, y, filenames):
        if filenames:
            self.frame.add_files(filenames)
        return True


class SubtitleCheckPopup(wx.ComboPopup):
    def __init__(self):
        super().__init__()
        self.combo = None
        self.checklist: wx.CheckListBox | None = None

    def Init(self):
        self.checklist = None

    def Create(self, parent):
        self.checklist = wx.CheckListBox(parent, choices=[])
        self.checklist.Bind(wx.EVT_CHECKLISTBOX, self.on_check)
        return True

    def GetControl(self):
        return self.checklist

    def SetStringValue(self, value):
        return

    def GetStringValue(self):
        return self.combo.GetValue() if self.combo else ""

    def GetAdjustedSize(self, min_width, pref_height, max_height):
        height = min(max_height, max(self.combo.FromDIP(80), min(self.combo.FromDIP(220), pref_height))) if self.combo else pref_height
        return wx.Size(max(min_width, self.combo.FromDIP(240) if self.combo else min_width), height)

    def on_check(self, event):
        if self.combo:
            self.combo.update_summary()
        event.Skip()


class SubtitleCheckCombo(wx.ComboCtrl):
    def __init__(self, parent, choices: list[str]):
        super().__init__(parent, style=wx.CB_READONLY)
        self.choices = choices
        self.popup = SubtitleCheckPopup()
        self.SetPopupControl(self.popup)
        self.popup.combo = self
        self.SetValue("Нет субтитров" if not choices else "Не выбраны")
        wx.CallAfter(self.populate_popup)

    def populate_popup(self):
        checklist = self.popup.checklist
        if not checklist:
            return
        checklist.Set(self.choices)
        self.update_summary()

    def GetCheckedItems(self) -> list[int]:
        checklist = self.popup.checklist
        if not checklist:
            return []
        return [i for i in range(checklist.GetCount()) if checklist.IsChecked(i)]

    def update_summary(self):
        checked = self.GetCheckedItems()
        if not self.choices:
            text = "Нет субтитров"
        elif not checked:
            text = "Не выбраны"
        else:
            text = f"Выбраны: {len(checked)}"
        self.SetValue(text)


# --- Основное окно приложения ---
class VideoConverter(wx.Frame):
    COL_FILE = 0
    COL_RES = 1
    COL_BR = 2
    COL_SIZE = 3
    COL_TIME = 4
    COL_AUDIO = 5
    COL_SUBTITLES = 6
    COL_SETTINGS = 7
    COL_STATUS = 8
    COL_PROGRESS = 9

    def __init__(self):
        super().__init__(
            None,
            title=f"Video Converter {__VERSION__}",
            style=(wx.DEFAULT_FRAME_STYLE | wx.WANTS_CHARS),
        )
        self.Bind(wx.EVT_CLOSE, self.on_close)

        panel = wx.Panel(self)
        panel.SetDropTarget(FileDropTarget(self))

        # состояние
        self.row_widgets: dict[int, dict] = {}
        self.converting = False
        self.process: subprocess.Popen | None = None
        self.cancel_event = threading.Event()
        self.queue_thread: threading.Thread | None = None
        self.all_jobs_duration = 0.0
        self.done_duration = 0.0
        self.current_output_file: str | None = None
        self.save_folder: str | None = None

        self.qp_value = 22
        self.bitrate_value = 8
        self.log_visible = False

        # layout
        vbox = wx.BoxSizer(wx.VERTICAL)

        # кнопки добавления/удаления/очистки
        top = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_add = wx.Button(panel, label="Добавить файлы...")
        self.btn_add.Bind(wx.EVT_BUTTON, self.browse_files)

        self.btn_remove = wx.Button(panel, label="Удалить")
        self.btn_remove.Bind(wx.EVT_BUTTON, self.on_remove_selected)

        self.btn_clear = wx.Button(panel, label="Очистить")
        self.btn_clear.Bind(wx.EVT_BUTTON, self.on_clear)

        self.save_folder_label = wx.StaticText(panel, label="Сохранять в: ", size=self.FromDIP(wx.Size(-1, 28)))
        self.save_folder_txt = wx.TextCtrl(panel, style=wx.TE_READONLY, size=self.FromDIP(wx.Size(120, -1)))
        self.btn_save_folder_browse = wx.Button(panel, label="Выбрать папку...")
        self.btn_save_folder_browse.Bind(wx.EVT_BUTTON, self.browse_save_folder)

        self.toggle_suffix = wx.ToggleButton(panel, label="_conv", size=self.FromDIP(wx.Size(60, -1)))
        self.toggle_suffix.SetToolTip("Добавлять суффикс к имени файла после конвертации")
        self.toggle_suffix.SetValue(True)

        basket_icon = wx.ArtProvider.GetBitmap(wx.ART_DELETE, size=wx.Size(16, 16))
        self.btn_clear_save_folder = wx.BitmapButton(panel, bitmap=basket_icon, size=self.FromDIP(wx.Size(22, 22)))
        self.btn_clear_save_folder.SetToolTip(
            "Очистить путь к папке для сохранения.\nСконвертированные файлы будут сохранены в папке с исходными файлами."
        )
        self.btn_clear_save_folder.Bind(wx.EVT_BUTTON, self.on_clear_save_folder)

        question_bmp = wx.ArtProvider.GetBitmap(wx.ART_HELP, size=wx.Size(16, 16))
        self.btn_info_page = wx.BitmapButton(panel, bitmap=question_bmp, size=self.FromDIP(wx.Size(22, 22)))
        self.btn_info_page.SetToolTip("Справка")
        self.btn_info_page.Bind(wx.EVT_BUTTON, self.on_info_page)

        top.Add(self.btn_add, 0, wx.ALL, self.FromDIP(8))
        top.Add(self.btn_remove, 0, wx.RIGHT | wx.TOP | wx.BOTTOM, self.FromDIP(8))
        top.Add(self.btn_clear, 0, wx.RIGHT | wx.TOP | wx.BOTTOM, self.FromDIP(8))
        top.AddStretchSpacer(1)
        top.Add(self.save_folder_label, 0, wx.RIGHT | wx.TOP | wx.BOTTOM | wx.ALIGN_CENTER_VERTICAL, self.FromDIP(8))
        top.Add(self.save_folder_txt, 1, wx.RIGHT | wx.TOP | wx.BOTTOM, self.FromDIP(8))
        top.Add(self.btn_save_folder_browse, 0, wx.TOP | wx.BOTTOM, self.FromDIP(8))
        top.Add(self.btn_clear_save_folder, 0, wx.RIGHT | wx.TOP | wx.BOTTOM, self.FromDIP(8))
        top.Add(self.toggle_suffix, 0, wx.TOP | wx.BOTTOM, self.FromDIP(8))
        top.Add(self.btn_info_page, 0, wx.ALL, self.FromDIP(8))

        vbox.Add(top, 0, wx.EXPAND)

        # UltimateListCtrl - список файлов
        self.list = ULC.UltimateListCtrl(
            panel,
            agwStyle=(
                wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES | wx.LC_NO_SORT_HEADER | ULC.ULC_HAS_VARIABLE_ROW_HEIGHT | ULC.ULC_SHOW_TOOLTIPS
            ),
        )

        self.list.InsertColumn(self.COL_FILE, "Файл", width=self.FromDIP(360))
        self.list.InsertColumn(self.COL_RES, "Разрешение", width=self.FromDIP(110))
        self.list.InsertColumn(self.COL_BR, "Битрейт", width=self.FromDIP(110))
        self.list.InsertColumn(self.COL_SIZE, "Размер", width=self.FromDIP(100))
        self.list.InsertColumn(self.COL_TIME, "Длительность", width=self.FromDIP(100))
        self.list.InsertColumn(self.COL_AUDIO, "Аудио дорожка", width=self.FromDIP(280))
        self.list.InsertColumn(self.COL_SUBTITLES, "Субтитры", width=self.FromDIP(240))
        self.list.InsertColumn(self.COL_SETTINGS, "Параметры", width=self.FromDIP(170))
        self.list.InsertColumn(self.COL_STATUS, "Статус", width=self.FromDIP(110))
        self.list.InsertColumn(self.COL_PROGRESS, "Прогресс", width=self.FromDIP(160))
        self.list.SetColumnShown(self.COL_SUBTITLES, False)

        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_play_file)
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_item_select)
        self.list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_right_click)
        self.list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.on_item_deselect)

        vbox.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(5))

        # --- encode_mode + quality на одной строке ---
        encode_row = wx.BoxSizer(wx.HORIZONTAL)

        # режим кодирования (слева)
        self.encode_mode = wx.RadioBox(
            panel,
            label="Режим кодирования",
            choices=["🎯 Постоянное качество (QP)", "📦 Постоянный битрейт (CBR)"],
            majorDimension=2,
            style=wx.RA_SPECIFY_COLS | wx.NO_BORDER,
        )
        self.encode_mode.SetSelection(0)
        self.encode_mode.Bind(wx.EVT_RADIOBOX, self.on_mode_change)

        # чтобы RadioBox не раздувал строку и выглядел аккуратно
        self.encode_mode.SetMinSize(self.FromDIP(wx.Size(430, -1)))
        self.encode_mode.SetToolTip("""QP — уровень качества видео для NVENC.
Меньше значение = лучше качество и больше размер файла.
Больше значение = сильнее сжатие и меньше размер файла.
Обычно разумный диапазон: 18–28

CBR — постоянный битрейт видео.
Чем выше значение, тем лучше качество и больше размер файла.
Чем ниже значение, тем сильнее сжатие и меньше размер файла.
Подходит, когда нужен предсказуемый размер или потоковая передача.""")

        encode_row.Add(self.encode_mode, 0, wx.ALL | wx.ALIGN_TOP, self.FromDIP(5))

        # слайдер качества (справа)

        self.slider_label = wx.StaticText(panel, label="Качество, QP:", size=self.FromDIP(wx.Size(90, -1)))
        self.qp_slider = wx.Slider(
            panel,
            minValue=14,
            maxValue=30,
            value=22,
            style=wx.SL_HORIZONTAL,
            size=self.FromDIP(wx.Size(360, 25)),
        )
        self.qp_label = wx.StaticText(panel, label="QP = 22", size=self.FromDIP(wx.Size(150, -1)))
        self.qp_slider.Bind(wx.EVT_SLIDER, self.on_qp_change)

        vbox_quality = wx.BoxSizer(wx.HORIZONTAL)
        vbox_quality.Add(self.slider_label, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, self.FromDIP(8))
        vbox_quality.Add(self.qp_slider, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, self.FromDIP(10))
        vbox_quality.Add(self.qp_label, 0, wx.ALIGN_CENTER_VERTICAL)

        # растягиваем правую часть
        encode_row.Add(vbox_quality, 1, wx.ALL | wx.EXPAND | wx.ALIGN_TOP, self.FromDIP(8))

        # добавляем всю строку в главный vbox
        vbox.Add(encode_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(5))

        # опции
        options_box = wx.BoxSizer(wx.HORIZONTAL)

        self.chk_limit_res = wx.CheckBox(panel, label="Ограничивать разрешение до FullHD (1920×1080)")
        self.chk_limit_res.SetValue(False)
        self.chk_limit_res.Bind(wx.EVT_CHECKBOX, self.on_limit_res)
        options_box.Add(self.chk_limit_res, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(10))

        self.tonemapping_label = wx.StaticText(panel, label="HDR→SDR:")
        options_box.Add(self.tonemapping_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(2))

        self.choice_tonemap = wx.Choice(panel, choices=["Авто", "Вкл", "Выкл"])
        self.choice_tonemap.SetSelection(0)
        self.choice_tonemap.Bind(wx.EVT_CHOICE, self.on_tonemapping)
        options_box.Add(self.choice_tonemap, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(10))

        self.chk_skip_video = wx.CheckBox(panel, label="не конв. видео")
        self.chk_skip_video.SetToolTip(wx.ToolTip("Не конвертировать видео"))
        self.chk_skip_video.SetValue(False)
        self.chk_skip_video.Bind(wx.EVT_CHECKBOX, self.on_skip_video)
        options_box.Add(self.chk_skip_video, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(5))

        self.chk_skip_audio = wx.CheckBox(panel, label="не конв. аудио")
        self.chk_skip_audio.SetToolTip(wx.ToolTip("Не конвертировать аудио"))
        self.chk_skip_audio.SetValue(False)
        self.chk_skip_audio.Bind(wx.EVT_CHECKBOX, self.on_skip_audio)
        options_box.Add(self.chk_skip_audio, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(5))

        self.chk_copy_tags = wx.CheckBox(panel, label="копировать теги")
        self.chk_copy_tags.SetToolTip(
            wx.ToolTip("Скопировать теги из исходного файла mp4 в cконвертированный файл. Это глобальная настройка.")
        )
        self.chk_copy_tags.SetValue(False)
        options_box.Add(self.chk_copy_tags, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(5))

        self.chk_save_subtitles = wx.CheckBox(panel, label="сохранить субтитры")
        self.chk_save_subtitles.SetToolTip(wx.ToolTip("Показать колонку субтитров и сохранить отмеченные дорожки в MP4."))
        self.chk_save_subtitles.SetValue(False)
        self.chk_save_subtitles.Bind(wx.EVT_CHECKBOX, self.on_save_subtitles)
        options_box.Add(self.chk_save_subtitles, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(5))

        self.chk_debug = wx.CheckBox(panel, label="Debug")
        self.chk_debug.SetValue(False)
        options_box.Add(self.chk_debug, 0, wx.ALIGN_CENTER_VERTICAL)
        self.min_client_width_to_debug = options_box.CalcMin().width + self.FromDIP(20)

        vbox.Add(options_box, 0, wx.LEFT | wx.TOP | wx.RIGHT | wx.BOTTOM, self.FromDIP(10))

        # кнопки запуска и открытия лога
        btn_box = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_start = wx.Button(panel, label="▶ Начать конвертацию")
        self.btn_start.Bind(wx.EVT_BUTTON, self.on_convert)

        self.btn_toggle_log = wx.Button(panel, label="📋 Показать лог", size=self.FromDIP(wx.Size(110, 28)))
        self.btn_toggle_log.SetToolTip("Показать/Скрыть лог")
        self.btn_toggle_log.Bind(wx.EVT_BUTTON, self.on_toggle_log)

        btn_box.Add(self.btn_start, 1, wx.ALL | wx.EXPAND, self.FromDIP(5))
        btn_box.Add(self.btn_toggle_log, 0, wx.ALL, self.FromDIP(5))
        vbox.Add(btn_box, 0, wx.EXPAND)

        # прогрессбар
        self.progress = wx.Gauge(panel, range=100, size=self.FromDIP(wx.Size(-1, 25)), style=wx.GA_HORIZONTAL | wx.GA_PROGRESS)
        vbox.Add(self.progress, 0, wx.EXPAND | wx.ALL, self.FromDIP(5))

        # прогресс и статус
        self.progress_label = wx.StaticText(panel, label="Прогресс: 0%")
        vbox.Add(self.progress_label, 0, wx.LEFT | wx.BOTTOM, self.FromDIP(5))

        # лог
        self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2, size=self.FromDIP(wx.Size(-1, 200)))
        self.log.Hide()  # скрыть по умолчанию
        vbox.Add(self.log, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(5))

        panel.SetSizer(vbox)

        self.size_no_log = self.FromDIP(wx.Size(1535, 670))
        self.size_log = self.FromDIP(wx.Size(1535, 875))  # +205
        self.SetSize(self.size_no_log)
        self.apply_min_window_size(self.size_no_log)
        icon_path = get_resource_path("images/favicon.png")
        if os.path.isfile(icon_path):
            try:
                self.SetIcon(wx.Icon(icon_path))
            except Exception:
                pass
        self.Centre()

        # проверка ffmpeg/ffprobe
        if not os.path.isfile(FFMPEG_PATH):
            self.log.AppendText("❌ Не найден ffmpeg.exe\n")
            self.btn_start.Disable()
        if not os.path.isfile(FFPROBE_PATH):
            self.log.AppendText("❌ Не найден ffprobe.exe\n")
            self.btn_start.Disable()
        ffmpeg_ver = get_ffmpeg_version(FFMPEG_PATH)
        if ffmpeg_ver != "FFmpeg не установлен":
            self.log.AppendText(f"✅ FFmpeg: {ffmpeg_ver['ffmpeg']}, Libavcodec: {ffmpeg_ver['libavcodec']}\n")

        # загрузка папки для сохранения
        _save_path = get_reg("save_path")
        if _save_path and os.path.isdir(_save_path):
            self.save_folder_txt.SetValue(_save_path)
            self.save_folder = _save_path

        self.Show()

    # --- UI actions ---
    def browse_files(self, event):
        with wx.FileDialog(
            self,
            "Выбери видеофайлы",
            wildcard="Видео файлы (*.mkv;*.mp4;*.mov;*.avi)|*.mkv;*.mp4;*.mov;*.avi",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.add_files(dlg.GetPaths())

    def add_files(self, paths: list[str]):
        for path in paths:
            if not path or not os.path.isfile(path):
                continue

            self.log.AppendText(f"{'-' * 30}\nДобавлен файл: {path}\n")

            tracks = get_audio_tracks(path)
            subtitles = get_subtitle_tracks(path)
            info = get_video_info(path)

            self.log.AppendText(
                "🎥 Видео:\n"
                f"🔹Кодек: {info['codec']}\n"
                f"🔹Разрешение: {info['width']}×{info['height']}\n"
                f"🔹FPS: {info['fps']}\n"
                f"🔹Соотношение сторон: {info['aspect']}\n"
                f"🔹Битрейт: {info['bitrate']}\n"
                f"🔹Тип: {info['hdr_type']}\n"
                f"🔹Длительность: {format_time(info['duration'])} ({info['duration']:.1f} сек)\n"
            )
            subtitle_types = Counter(str(track.get("codec", "?")) for track in subtitles)
            subtitle_info = ", ".join(f"{codec}: {count}" for codec, count in subtitle_types.items()) if subtitle_types else "нет"
            self.log.AppendText(f"💬 Субтитры: {len(subtitles)} ({subtitle_info})\n")

            self.add_row(
                path=path,
                resolution=f"{info['width']}×{info['height']}",
                bitrate=str(info["bitrate"]),
                duration=float(info["duration"] or 0.0),
                size_bytes=int(info["size"] or 0),
                audio_choices=tracks,
                subtitle_tracks=subtitles,
            )

    def on_remove_selected(self, event):
        if self.converting:
            wx.MessageBox("Нельзя удалять строки во время конвертации.", "Внимание", wx.OK | wx.ICON_WARNING)
            return

        row = self.list.GetFirstSelected()
        if row == -1:
            return

        self.delete_row(row)

    def on_clear(self, event):
        if self.converting:
            wx.MessageBox("Нельзя очищать список во время конвертации.", "Внимание", wx.OK | wx.ICON_WARNING)
            return

        # уничтожаем виджеты
        for row in list(self.row_widgets.keys()):
            w = self.row_widgets[row]
            for key in ("choice", "subtitles", "gauge"):
                try:
                    ctrl = w.get(key)
                    if ctrl:
                        ctrl.Destroy()
                except Exception:
                    pass

        self.list.DeleteAllItems()
        self.row_widgets.clear()
        self.log.AppendText("\n🧹 Список очищен.\n")

    def delete_row(self, row: int):
        w = self.row_widgets.get(row)
        if w:
            try:
                if w.get("choice"):
                    w["choice"].Destroy()
            except Exception:
                pass
            try:
                if w.get("subtitles"):
                    w["subtitles"].Destroy()
            except Exception:
                pass
            try:
                if w.get("gauge"):
                    w["gauge"].Destroy()
            except Exception:
                pass

        self.list.DeleteItem(row)

        # пересобираем row_widgets с новыми индексами
        new_map: dict[int, dict] = {}
        for i in range(self.list.GetItemCount()):
            # после DeleteItem виджеты “остаются” в контроле, мы их держим в старых dict — надо сдвинуть
            if i < row:
                new_map[i] = self.row_widgets[i]
            else:
                new_map[i] = self.row_widgets[i + 1]
        self.row_widgets = new_map

    def on_mode_change(self, event):
        mode = self.encode_mode.GetSelection()
        if mode == 0:
            self.slider_label.SetLabel("Качество, QP:")
            self.qp_slider.SetRange(14, 30)
            self.qp_slider.SetValue(22)
            self.qp_label.SetLabel("QP = 22")
            self.qp_value = 22
        else:
            self.slider_label.SetLabel("Битрейт (Мбит/с):")
            self.qp_slider.SetRange(2, 25)
            self.qp_slider.SetValue(8)
            self.qp_label.SetLabel("Битрейт = 8.0 Мбит/с")
            self.bitrate_value = 8

        self.save_settings_to_sel_rows_and_update_list()

    def on_qp_change(self, event):
        mode = self.encode_mode.GetSelection()
        val = self.qp_slider.GetValue()
        if mode == 0:
            self.qp_value = val
            self.qp_label.SetLabel(f"QP = {val}")
        else:
            self.bitrate_value = val
            self.qp_label.SetLabel(f"Битрейт = {val:.1f} Мбит/с")

        self.save_settings_to_sel_rows_and_update_list()

    def on_mode_and_qp_reset(self):
        mode = self.encode_mode.GetSelection()
        if mode == 0:
            self.slider_label.SetLabel("Качество, QP:")
            self.qp_slider.SetRange(14, 30)
            self.qp_slider.SetValue(self.global_settings.get("qp_slider", 22))
            self.qp_label.SetLabel(f"QP = {self.global_settings.get('qp_slider', 22)}")
            self.qp_value = self.global_settings.get("qp_slider", 22)
        else:
            self.slider_label.SetLabel("Битрейт (Мбит/с):")
            self.qp_slider.SetRange(2, 25)
            self.qp_slider.SetValue(self.global_settings.get("qp_slider", 8))
            self.qp_label.SetLabel(f"Битрейт = {self.global_settings.get('qp_slider', 8)}")
            self.bitrate_value = self.global_settings.get("qp_slider", 8)

    def on_toggle_log(self, event):
        if self.log_visible:
            self.log.Hide()
            self.btn_toggle_log.SetLabel("📋 Показать лог")
        else:
            self.log.Show()
            self.btn_toggle_log.SetLabel("📋 Скрыть лог")
        self.log_visible = not self.log_visible
        self.update_window_size()

    def update_window_size(self):
        position = self.GetPosition()
        current_size = self.GetSize()
        log_delta = self.size_log.height - self.size_no_log.height
        height = current_size.height + log_delta if self.log_visible else current_size.height - log_delta
        if not self.log_visible:
            height = max(self.size_no_log.height, height)
        self.log.SetMinSize(wx.Size(-1, self.FromDIP(200)))
        self.apply_min_window_size(wx.Size(self.size_no_log.width, self.size_no_log.height))
        self.SetSize(position.x, position.y, current_size.width, height)
        self.SetPosition(position)
        self.Layout()

    def apply_min_window_size(self, base_size):
        min_size = wx.Size(self.ClientToWindowSize(wx.Size(self.min_client_width_to_debug, 0)).width, base_size.height)
        self.SetMinSize(min_size)

    def on_save_subtitles(self, event):
        enabled = self.chk_save_subtitles.GetValue()
        if enabled:
            for row in self.row_widgets:
                self.create_subtitle_widget(row)
        else:
            for widgets in self.row_widgets.values():
                subtitles = widgets.get("subtitles")
                if subtitles:
                    try:
                        subtitles.Destroy()
                    except Exception:
                        pass
                widgets["subtitles"] = None

        self.list.SetColumnShown(self.COL_SUBTITLES, enabled)
        self.Layout()

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
        self.save_settings_to_sel_rows_and_update_list()

    def on_key_down(self, event):
        if event.GetKeyCode() == wx.WXK_DELETE:
            self.on_remove_selected(event)

    def on_play_file(self, event):
        row = self.list.GetFirstSelected()
        if row == -1:
            return
        widgets = self.row_widgets.get(row)
        path = widgets.get("path")
        if os.path.isfile(path):
            audio_stream_num = widgets.get("choice").GetSelection() + 1
            subprocess.Popen(
                [
                    MPV_PATH,
                    path,
                    f"--aid={str(audio_stream_num)}",
                    (
                        "--title=${filename} — [Аудио #${current-tracks/audio/id}]:"
                        "${current-tracks/audio/title:Без названия} (${current-tracks/audio/lang:-}), "
                        "каналов: ${current-tracks/audio/audio-channels:?}, кодек: ${current-tracks/audio/codec}"
                    ),
                    "--no-sub",
                ],
            )

    def on_right_click(self, event):
        """Контекстное меню по правому клику"""
        # Получаем индекс строки из события
        item = event.GetIndex()

        # Показываем меню только если клик был на строке
        if item == wx.NOT_FOUND or item == -1:
            return

        # Выделяем строку, если она не выделена
        if not self.list.IsSelected(item):
            self.list.Select(item)

        # Создаем контекстное меню
        menu = wx.Menu()

        # Пункты меню
        play_item = menu.Append(wx.ID_ANY, "▶ Воспроизвести")
        play_converted_item = menu.Append(wx.ID_ANY, "▶ Воспроизвести сконвертированный файл")
        widgets = self.row_widgets.get(item)
        if self.list.GetItem(item, self.COL_STATUS).GetText() == "✅ Готово" and "output_file" in widgets:
            play_converted_item.Enable()
        else:
            play_converted_item.Enable(False)
        menu.AppendSeparator()

        if not widgets.get("settings", "").get("global", False):
            reset_convert_settings_item = menu.Append(wx.ID_ANY, "🔄 Сбросить настройки конвертации")
            menu.AppendSeparator()
            self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.reset_convert_settings, e), reset_convert_settings_item)

        open_folder_item = menu.Append(wx.ID_ANY, "📁 Открыть папку с файлом")
        open_output_folder_item = menu.Append(wx.ID_ANY, "📂 Открыть папку вывода")
        menu.AppendSeparator()

        remove_item = menu.Append(wx.ID_ANY, "🗑 Удалить из списка")
        clear_item = menu.Append(wx.ID_ANY, "🧹 Очистить весь список")

        # Отключаем пункты, если идет конвертация
        if self.converting:
            remove_item.Enable(False)
            clear_item.Enable(False)

        # Привязываем обработчики
        self.Bind(wx.EVT_MENU, lambda e: self.on_play_file(e), play_item)
        self.Bind(wx.EVT_MENU, lambda e: self.on_context_open_folder(e), open_folder_item)
        self.Bind(wx.EVT_MENU, lambda e: self.on_context_open_output_folder(e), open_output_folder_item)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.on_remove_selected, e), remove_item)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.on_clear, e), clear_item)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.on_context_play_converted, e), play_converted_item)

        # Показываем меню в позиции курсора
        self.list.PopupMenu(menu)
        menu.Destroy()

    def on_context_open_folder(self, event):
        """Открыть папку с исходным файлом"""
        row = self.list.GetFirstSelected()
        if row == -1:
            return

        widgets = self.row_widgets.get(row)
        if not widgets:
            return

        path = widgets.get("path")
        if path and os.path.isfile(path):
            subprocess.Popen(f'explorer /select,"{path}"')

    def on_context_open_output_folder(self, event):
        """Открыть папку вывода"""
        row = self.list.GetFirstSelected()
        if row == -1:
            return
        widgets = self.row_widgets.get(row)
        path = widgets.get("output_file", "")
        if path and os.path.isfile(path):
            subprocess.Popen(f'explorer /select,"{path}"')

    def on_context_play_converted(self, event):
        row = self.list.GetFirstSelected()
        if row == -1:
            return
        widgets = self.row_widgets.get(row)
        if self.list.GetItem(row, self.COL_STATUS).GetText() == "✅ Готово" and "output_file" in widgets:
            output_file = widgets.get("output_file")
            if os.path.isfile(output_file):
                subprocess.Popen(
                    [
                        MPV_PATH,
                        output_file,
                        "--title=Сконвертированный файл: ${filename}",
                        "--no-sub",
                    ],
                )
                return

    # --- Rows ---
    def add_row(
        self,
        path: str,
        resolution: str,
        bitrate: str,
        duration: float,
        size_bytes: int,
        audio_choices: list[str],
        subtitle_tracks: list[dict],
    ):
        row = self.list.GetItemCount()

        filename = os.path.basename(path)
        self.list.InsertStringItem(row, filename)

        self.list.SetStringItem(row, self.COL_RES, resolution)
        self.list.SetStringItem(row, self.COL_BR, bitrate)
        self.list.SetStringItem(row, self.COL_TIME, format_time(duration))
        self.list.SetStringItem(row, self.COL_SIZE, human_size(size_bytes))
        self.list.SetStringItem(row, self.COL_STATUS, "Ожидает")
        self.list.SetStringItem(row, self.COL_SETTINGS, "⚙️Глобальные")

        choice = wx.Choice(self.list, choices=audio_choices)
        if audio_choices:
            choice.SetSelection(0)
        self.list.SetItemWindow(row, self.COL_AUDIO, choice, expand=True)

        gauge = wx.Gauge(self.list, range=100, size=self.FromDIP(wx.Size(-1, 18)), style=wx.GA_HORIZONTAL)
        gauge.SetValue(0)
        self.list.SetItemWindow(row, self.COL_PROGRESS, gauge, expand=True)

        self.row_widgets[row] = {
            "path": path,
            "choice": choice,
            "subtitles": None,
            "subtitle_tracks": subtitle_tracks,
            "gauge": gauge,
            "duration": float(duration or 0.0),
            "settings": {
                "global": True,
                "encode_mode": "",
                "qp_slider": "",
                "limit_res": "",
                "tonemapping": "",
                "skip_video": "",
                "skip_audio": "",
            },
        }
        if self.chk_save_subtitles.GetValue():
            self.create_subtitle_widget(row)

    def create_subtitle_widget(self, row: int):
        widgets = self.row_widgets.get(row)
        if not widgets or widgets.get("subtitles"):
            return
        subtitle_choices = [track["display"] for track in widgets.get("subtitle_tracks", [])]
        subtitles = SubtitleCheckCombo(self.list, choices=subtitle_choices)
        self.list.SetItemWindow(row, self.COL_SUBTITLES, subtitles, expand=True)
        widgets["subtitles"] = subtitles

    # --- Queue ---
    def on_convert(self, event):
        if self.converting:
            self.cancel_conversion()
            return

        if not self.row_widgets:
            self.log.AppendText("\n⚠ Нет файлов в очереди.\n")
            return

        self.all_jobs_duration = sum(float(self.row_widgets[r].get("duration") or 0.0) for r in self.row_widgets)
        self.done_duration = 0.0
        self.cancel_event.clear()
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
            for row in sorted(self.row_widgets.keys()):
                if self.cancel_event.is_set():
                    break

                widgets = self.row_widgets[row]
                path = widgets.get("path")
                duration = float(widgets.get("duration") or 0.0)
                gauge: wx.Gauge | None = widgets.get("gauge")
                choice: wx.Choice | None = widgets.get("choice")
                if not path or not os.path.isfile(path):
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "❌ Нет файла")
                    if gauge:
                        wx.CallAfter(gauge.SetValue, 0)
                    continue

                selected_track = choice.GetSelection() if choice else wx.NOT_FOUND
                if selected_track == wx.NOT_FOUND:
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "❌ Нет аудио")
                    if gauge:
                        wx.CallAfter(gauge.SetValue, 0)
                    continue

                audio_channels = get_audio_channels(path, selected_track)
                bitrate = get_audio_bitrate(audio_channels)
                output_file = unique_output_path(self.save_folder, path, self.toggle_suffix.GetValue())
                selected_subtitles = self.get_selected_subtitles(widgets) if self.chk_save_subtitles.GetValue() else []

                wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "⏳ Конвертация...")
                if gauge:
                    wx.CallAfter(gauge.SetValue, 0)

                wx.CallAfter(self.log.AppendText, f"\n{'-' * 30}\nНачало конвертации...\n🎬 Файл: {path}\n➡ Выход: {output_file}\n")
                self.current_output_file = output_file

                settings = self.row_widgets[row]["settings"]

                ok = self.run_ffmpeg_with_progress(
                    input_path=path,
                    output_path=output_file,
                    selected_track=selected_track,
                    bitrate=bitrate,
                    audio_channels=audio_channels,
                    selected_subtitles=selected_subtitles,
                    duration=duration,
                    gauge=gauge,
                    settings=settings,
                )

                if ok and not self.cancel_event.is_set():
                    widgets.update({"output_file": output_file})
                    if self.chk_copy_tags.GetValue() and os.path.splitext(path)[1].lower() == ".mp4":
                        if copy_mp4_tags(path, output_file):
                            wx.CallAfter(self.log.AppendText, "📌 Теги скопированы\n")
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "✅ Готово")
                    wx.CallAfter(gauge.SetValue, 100)
                    wx.CallAfter(self.log.AppendText, "\n ✅ Конвертация завершена\n")
                    self.done_duration += duration
                elif self.cancel_event.is_set():
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "⏹ Отменено")
                    wx.CallAfter(gauge.SetValue, 100)
                    break
                else:
                    wx.CallAfter(self.list.SetStringItem, row, self.COL_STATUS, "❌ Ошибка")
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

    def on_item_select(self, event):
        self.global_settings = self.get_current_settings()

    def get_selected_subtitles(self, widgets: dict) -> list[dict]:
        subtitle_list: SubtitleCheckCombo | None = widgets.get("subtitles")
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
    def run_ffmpeg_with_progress(
        self,
        input_path: str,
        output_path: str,
        selected_track: int,
        bitrate: str,
        audio_channels: int,
        selected_subtitles: list[dict],
        duration: float,
        gauge: wx.Gauge | None,
        settings: dict,
    ) -> bool:

        if not settings.get("global", True):
            chk_skip_audio = settings.get("skip_audio", False)
            chk_skip_video = settings.get("skip_video", False)
            encode_mode = settings.get("encode_mode", 0)
            qp_slider = settings.get("qp_slider", 22)
            chk_limit_res = settings.get("limit_res", False)
            tonemap_mode = settings.get("tonemapping", 0)
        else:
            global_settings = self.get_current_settings()
            chk_skip_audio = global_settings.get("skip_audio", False)
            chk_skip_video = global_settings.get("skip_video", False)
            encode_mode = global_settings.get("encode_mode", 0)
            qp_slider = global_settings.get("qp_slider", 22)
            chk_limit_res = global_settings.get("limit_res", False)
            tonemap_mode = global_settings.get("tonemapping", 0)

        # audio
        if chk_skip_audio:
            audio_codec_args = ["-c:a", "copy"]
            wx.CallAfter(self.log.AppendText, "🎵 Аудио: copy\n")
        else:
            audio_codec_args = ["-c:a", "aac", "-ac", str(audio_channels), "-b:a", bitrate]
            wx.CallAfter(self.log.AppendText, f"🎵 Аудио: AAC, {audio_channels}ch, {bitrate}\n")

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

        # video
        if not chk_skip_video:
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
            if chk_limit_res:
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

            if encode_mode == 0:
                video_codec_args = ["-rc", "vbr", "-cq", str(qp_slider), "-b:v", "0", "-qmin", "0"]
                wx.CallAfter(self.log.AppendText, f"🎯 Режим: QP={qp_slider}\n")
            else:
                target_bitrate = f"{int(qp_slider * 1000)}k"
                video_codec_args = ["-b:v", target_bitrate, "-maxrate", target_bitrate, "-bufsize", "2M"]
                wx.CallAfter(self.log.AppendText, f"📦 Режим: CBR={target_bitrate}\n")

            cmd = [
                FFMPEG_PATH,
                "-hide_banner",
                "-y",
                "-i",
                input_path,
                "-map",
                "0:v:0",
                "-map",
                f"0:a:{selected_track}",
                *subtitle_map_args,
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
                "-b_ref_mode",
                "middle",
                "-spatial_aq",
                "1",
                *audio_codec_args,
                "-map_metadata",
                "-1",
                *subtitle_metadata_args,
                "-bsf:v",  # удаление скрытых субтитров (Closed captions EIA-608/CEA-608)
                "filter_units=remove_types=6",
                *subtitle_codec_args,
                output_path,
            ]
        else:
            wx.CallAfter(self.log.AppendText, "🎥 Видео: copy\n")
            cmd = [
                FFMPEG_PATH,
                "-hide_banner",
                "-y",
                "-i",
                input_path,
                "-map",
                "0:v:0",
                "-map",
                f"0:a:{selected_track}",
                *subtitle_map_args,
                "-c:v",
                "copy",
                *audio_codec_args,
                "-map_metadata",
                "-1",
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
                stdout=subprocess.PIPE,
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

        current_speed = "?"
        current_fps = "?"

        for line in self.process.stderr:
            if self.cancel_event.is_set():
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

            seconds_to_convert = self.all_jobs_duration - overall
            try:
                remaining_time = format_time(seconds_to_convert / float(current_speed))
            except Exception:
                remaining_time = "?"

            wx.CallAfter(self.progress.SetValue, overall_progress)
            if gauge:
                wx.CallAfter(gauge.SetValue, row_progress)

            wx.CallAfter(
                self.progress_label.SetLabel,
                f"Очередь: {overall_progress}% │ Файл: {row_progress}% │ ⚡ {current_speed}x │ 🎞️ {current_fps} fps | ⏲ {remaining_time}",
            )

        # cancel
        if self.cancel_event.is_set():
            try:
                self.process.terminate()
                time.sleep(0.3)
            except Exception:
                pass

        rc = self.process.wait() if self.process else -1
        if self.cancel_event.is_set():
            return False

        if rc != 0:
            wx.CallAfter(self.log.AppendText, f"❌ FFmpeg завершился с кодом {rc}\n")
            return False

        return True

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
            self.cancel_conversion()
        self.Destroy()

    def disable_interface(self):
        self.btn_add.Disable()
        self.btn_remove.Disable()
        self.btn_clear.Disable()
        self.qp_slider.Disable()
        self.encode_mode.Disable()
        self.btn_save_folder_browse.Disable()
        self.chk_save_subtitles.Disable()

    def enable_interface(self):
        self.btn_add.Enable()
        self.btn_remove.Enable()
        self.btn_clear.Enable()
        self.qp_slider.Enable()
        self.encode_mode.Enable()
        self.btn_save_folder_browse.Enable()
        self.chk_save_subtitles.Enable()

    def browse_save_folder(self, event):
        with wx.DirDialog(
            self,
            "Выберите папку для сохранения конвертируемых файлов",
            style=wx.DD_DEFAULT_STYLE,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                path = dlg.GetPath()
                self.save_folder_txt.SetValue(path)
                self.save_folder = path
                save_reg("save_path", path)

    def get_current_settings(self):
        return {
            "global": False,
            "encode_mode": self.encode_mode.GetSelection(),
            "qp_slider": self.qp_slider.GetValue(),
            "limit_res": self.chk_limit_res.GetValue(),
            "tonemapping": self.choice_tonemap.GetSelection(),
            "skip_video": self.chk_skip_video.GetValue(),
            "skip_audio": self.chk_skip_audio.GetValue(),
        }

    def reset_global_settings(self):
        if self.global_settings:
            self.encode_mode.SetSelection(self.global_settings.get("encode_mode", 0))
            self.qp_slider.SetValue(self.global_settings.get("qp_slider", 22))
            self.on_mode_and_qp_reset()
            self.chk_limit_res.SetValue(self.global_settings.get("limit_res", False))
            self.choice_tonemap.SetSelection(self.global_settings.get("tonemapping", 0))
            self.chk_skip_video.SetValue(self.global_settings.get("skip_video", False))
            if self.global_settings.get("skip_video", False):
                self.on_skip_video(None)
            self.chk_skip_audio.SetValue(self.global_settings.get("skip_audio", False))

    def set_settings_to_selected_rows(self):
        item_index = self.list.GetFirstSelected()
        while item_index != -1:
            item_text = self.list.GetItemText(item_index)
            print(f"Выбрана строка {item_index}: {item_text}")

            item_index = self.list.GetNextSelected(item_index)

    def get_row_settings_string(self, row: int, settings: dict):
        if settings.get("skip_video", False):
            video_str = "В: не конв."
        else:
            if settings.get("encode_mode", 0) == 0:
                video_str = f"QP={settings.get('qp_slider', 22)}"
            else:
                video_str = f"CBR={settings.get('qp_slider', 8)}"
            if settings.get("limit_res", False):
                video_str += ", fullHD"
            tm_string = settings.get("tonemapping", 0)
            if tm_string == 2:
                video_str += ", TM=выкл"
            elif tm_string == 1:
                video_str += ", TM=вкл"
            elif tm_string == 0:
                video_str += ", TM=auto"

        if settings.get("skip_audio", False):
            audio_str = ", А: не конв."
        else:
            audio_str = ""
        return f"{video_str}{audio_str}"

    def save_settings_to_sel_rows_and_update_list(self):
        item_index = self.list.GetFirstSelected()
        while item_index != -1:
            settings = self.get_current_settings()
            self.row_widgets[item_index]["settings"] = settings
            self.list.SetStringItem(item_index, self.COL_SETTINGS, self.get_row_settings_string(item_index, settings))
            self.list.SetItemBackgroundColour(item_index, wx.Colour(255, 251, 235))
            self.list.Refresh()
            item_index = self.list.GetNextSelected(item_index)

    def on_limit_res(self, event):
        self.save_settings_to_sel_rows_and_update_list()

    def on_tonemapping(self, event):
        self.save_settings_to_sel_rows_and_update_list()

    def on_skip_audio(self, event):
        self.save_settings_to_sel_rows_and_update_list()

    def on_item_deselect(self, event):
        self.reset_global_settings()

    def on_clear_save_folder(self, event):
        self.save_folder_txt.SetValue("")
        save_reg("save_path", "")

    def reset_convert_settings(self, event):
        item_index = self.list.GetFirstSelected()
        if item_index == -1:
            return
        self.row_widgets[item_index]["settings"] = {
            "global": True,
            "encode_mode": "",
            "qp_slider": "",
            "limit_res": "",
            "tonemapping": "",
            "skip_video": "",
            "skip_audio": "",
        }
        self.list.SetStringItem(item_index, self.COL_SETTINGS, "⚙️Глобальные")
        self.list.SetItemBackgroundColour(item_index, wx.Colour(255, 255, 255))
        self.list.Refresh()

    def on_info_page(self, event):
        description = """\
Программа для быстрого перекодирования видео в формат MP4 с использованием аппаратного ускорителя видеокарт NVIDIA. Основана на FFmpeg.

Доступны следующие форматы входных файлов: MKV, MP4, MOV, AVI.
Выходной формат: MP4.
Видеокодек: NVENC (H.264), аудиокодек: AAC.
Настройки качества: режим постоянного качества (QP) или режим постоянного битрейта (CBR).
Работает только на компьютерах с видеокартой NVIDIA с поддержкой NVENC."""
        wx.Locale.AddCatalogLookupPathPrefix(".")
        rus_locale = wx.Locale(wx.LANGUAGE_RUSSIAN)  # noqa: F841
        info = AboutDialogInfo()
        info.SetName("Video Converter")
        info.SetVersion(__VERSION__)
        info.SetDescription(description)
        info.SetCopyright("(C) 2025-2026 Ванюнин Александр")
        info.SetLicence(read_from_txt(get_resource_path("LICENSE")))
        info.SetIcon(wx.Icon(get_resource_path("images/favicon.ico"), wx.BITMAP_TYPE_ICO))
        info.AddDeveloper("Код: Ванюнин Александр")
        info.AddDeveloper("идеи и тестирование: Колесников Дмитрий")
        info.SetWebSite("https://github.com/Fan4Metal/video_converter", "Github")
        wx.adv.AboutBox(info)


if __name__ == "__main__":
    app = wx.App()
    top = VideoConverter()
    app.MainLoop()
