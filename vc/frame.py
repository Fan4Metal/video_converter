"""Основное окно приложения."""
import os
import subprocess
import threading
from collections import Counter

import wx
from wx.adv import AboutDialogInfo
from wx.lib.agw import ultimatelistctrl as ULC

from vc.context_menu import ContextMenuMixin
from vc.conversion import ConversionMixin
from vc.estimate import estimate_output_size, format_estimate
from vc.ffmpeg_tools import check_nvenc_available, get_ffmpeg_version
from vc.marquee import MarqueeSelectionMixin
from vc.media_probe import parse_audio_tracks, parse_subtitle_tracks, parse_video_info, probe_media
from vc.probe import ProbeMixin
from vc.registry import get_reg, save_reg
from vc.resources import FFMPEG_PATH, FFPROBE_PATH, get_resource_path, read_from_txt
from vc.settings import RowSettings, format_row_settings
from vc.single_instance import InstanceServer
from vc.sorting import SortingMixin
from vc.utils import format_time, human_size
from vc.version import __VERSION__
from vc.widgets import FileDropTarget, SubtitleCheckCombo


class VideoConverter(MarqueeSelectionMixin, SortingMixin, ContextMenuMixin, ProbeMixin, ConversionMixin, wx.Frame):
    COL_FILE = 0
    COL_RES = 1
    COL_BR = 2
    COL_SIZE = 3
    COL_EST = 4
    COL_TIME = 5
    COL_AUDIO = 6
    COL_SUBTITLES = 7
    COL_SETTINGS = 8
    COL_STATUS = 9
    COL_PROGRESS = 10

    # Базовые заголовки столбцов (без стрелки сортировки)
    COL_LABELS = {
        COL_FILE: "Файл",
        COL_RES: "Разрешение",
        COL_BR: "Битрейт",
        COL_SIZE: "Размер",
        COL_EST: "Ожид. размер",
        COL_TIME: "Длительность",
        COL_AUDIO: "Аудио дорожка",
        COL_SUBTITLES: "Субтитры",
        COL_SETTINGS: "Параметры",
        COL_STATUS: "Статус",
        COL_PROGRESS: "Прогресс",
    }

    # Ширины столбцов по умолчанию в DIP. Столбец прогресса растягивается на остаток.
    COL_DEFAULT_WIDTHS = {
        COL_FILE: 360,
        COL_RES: 92,
        COL_BR: 85,
        COL_SIZE: 65,
        COL_EST: 101,
        COL_TIME: 100,
        COL_AUDIO: 280,
        COL_SUBTITLES: 240,
        COL_SETTINGS: 170,
        COL_STATUS: 110,
        COL_PROGRESS: 128,
    }
    # Запас к ширине заголовка и минимальная ширина растягиваемого столбца прогресса (DIP).
    COL_HEADER_PADDING = 6
    COL_PROGRESS_MIN = 90

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
        # row_widgets ключуется стабильным uid (не индексом строки).
        # row_order хранит uid в порядке отображения списка — это источник
        # правды для соответствия «индекс строки -> uid».
        self.row_widgets: dict[int, dict] = {}
        self.row_order: list[int] = []
        self._next_row_uid = 0
        # Состояние сортировки по клику на заголовок столбца
        self._sort_col: int | None = None
        self._sort_ascending = True
        self.converting = False
        self.process: subprocess.Popen | None = None
        self.cancel_event = threading.Event()
        # Пропуск текущего файла из контекстного меню (без остановки очереди)
        self.skip_event = threading.Event()
        self._current_uid: int | None = None  # uid строки, которая конвертируется сейчас
        self._queue_row = -1  # индекс строки, до которой дошла очередь
        self._instance_server: InstanceServer | None = None
        # Контрольная кодировка (оценка размера по фрагментам)
        self.probing = False
        self.probe_process: subprocess.Popen | None = None
        self.probe_cancel = threading.Event()
        self.queue_thread: threading.Thread | None = None
        self.all_jobs_duration = 0.0
        self.done_duration = 0.0
        self.current_output_file: str | None = None
        self.save_folder: str | None = None

        self.qp_value = 22
        self.bitrate_value = 8
        self.log_visible = False
        self.global_settings: RowSettings | None = None
        self.nvenc_available = True

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
                wx.LC_REPORT
                | wx.LC_HRULES
                | wx.LC_VRULES
                | wx.LC_NO_SORT_HEADER
                | ULC.ULC_HAS_VARIABLE_ROW_HEIGHT
                | ULC.ULC_SHOW_TOOLTIPS
                | ULC.ULC_NO_ITEM_DRAG
            ),
        )
        self._init_marquee_selection()

        for col in sorted(self.COL_DEFAULT_WIDTHS):
            self.list.InsertColumn(col, self.COL_LABELS[col], width=self._column_width(col))
        self.list.SetColumnShown(self.COL_SUBTITLES, False)

        self.list.Bind(wx.EVT_SIZE, self.on_list_size)
        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        self.list.Bind(wx.EVT_LIST_COL_CLICK, self.on_col_click)
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
Подходит, когда нужен предсказуемый размер или потоковая передача.

Столбец «Ожид. размер» показывает прогноз: точный расчёт для CBR (≈)
и приблизительную оценку для QP (~), которая уточняется во время кодирования.
Пункт контекстного меню «Оценить размер контрольной кодировкой» кодирует
несколько фрагментов файла и заменяет оценку точным замером (≈).""")

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

        # проверка аппаратного энкодера NVENC
        if os.path.isfile(FFMPEG_PATH):
            self.nvenc_available = check_nvenc_available(FFMPEG_PATH)
            if self.nvenc_available:
                self.log.AppendText("✅ NVENC (NVIDIA) доступен: используется аппаратное ускорение (h264_nvenc)\n")
            else:
                self.log.AppendText(
                    "⚠ NVENC недоступен (нет видеокарты NVIDIA или поддержки).\n"
                    "   Будет использовано программное кодирование на CPU (libx264) — медленнее.\n"
                )

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
        # ffprobe-анализ медленный — выполняем его в фоне, чтобы не блокировать UI.
        # Виджеты строк и лог создаются в UI-потоке через wx.CallAfter.
        # Во время конвертации файлы тоже добавляются: строка встаёт в конец
        # списка, и очередь подхватывает её после текущих.
        valid_paths = [p for p in paths if p and os.path.isfile(p)]
        if not valid_paths:
            return
        threading.Thread(target=self._probe_files_worker, args=(valid_paths,), daemon=True).start()

    # --- Единственный экземпляр: приём файлов от других процессов ---
    def start_instance_server(self):
        """Открывает локальный сокет, через который другие копии передают пути."""
        self._instance_server = InstanceServer(lambda paths: wx.CallAfter(self.add_files_from_shell, paths))
        self._instance_server.start()

    def add_files_from_shell(self, paths: list[str]):
        """Поднимает окно и добавляет файлы, пришедшие из командной строки."""
        if self.IsIconized():
            self.Iconize(False)
        self.Raise()
        self.RequestUserAttention()
        if paths:
            self.add_files(paths)

    def _probe_files_worker(self, paths: list[str]):
        """Фоновый поток: анализирует файлы ffprobe и передаёт результат в UI-поток."""
        for path in paths:
            try:
                probe = probe_media(path)  # один вызов ffprobe вместо четырёх
                tracks = parse_audio_tracks(probe)
                subtitles = parse_subtitle_tracks(probe)
                info = parse_video_info(probe)
            except Exception as e:
                wx.CallAfter(self.log.AppendText, f"⚠ Не удалось проанализировать файл {path}: {e}\n")
                continue
            wx.CallAfter(self._on_file_probed, path, tracks, subtitles, info)

    def _on_file_probed(self, path: str, tracks: list[str], subtitles: list[dict], info: dict):
        """Выполняется в UI-потоке: пишет лог и создаёт строку для проанализированного файла."""
        self.log.AppendText(f"{'-' * 30}\nДобавлен файл: {path}\n")

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
            video_info=info,
        )

    def _widgets_at(self, row: int) -> dict | None:
        """Виджеты строки по индексу отображения (через стабильный uid из row_order)."""
        if row is None or row < 0 or row >= len(self.row_order):
            return None
        return self.row_widgets.get(self.row_order[row])

    def on_remove_selected(self, event):
        if self.converting:
            wx.MessageBox("Нельзя удалять строки во время конвертации.", "Внимание", wx.OK | wx.ICON_WARNING)
            return

        rows = self._selected_rows()
        if not rows:
            return

        # Удаляем с конца, чтобы индексы оставшихся строк не сдвигались.
        for row in sorted(rows, reverse=True):
            self.delete_row(row)

    def on_clear(self, event):
        if self.converting:
            wx.MessageBox("Нельзя очищать список во время конвертации.", "Внимание", wx.OK | wx.ICON_WARNING)
            return

        # уничтожаем виджеты
        for w in self.row_widgets.values():
            for key in ("choice", "subtitles", "gauge"):
                try:
                    ctrl = w.get(key)
                    if ctrl:
                        ctrl.Destroy()
                except Exception:
                    pass

        self.list.DeleteAllItems()
        self.row_widgets.clear()
        self.row_order.clear()
        self._schedule_progress_fit()
        self.log.AppendText("\n🧹 Список очищен.\n")

    def delete_row(self, row: int):
        if row < 0 or row >= len(self.row_order):
            return
        uid = self.row_order[row]
        w = self.row_widgets.get(uid)
        if w:
            for key in ("choice", "subtitles", "gauge"):
                try:
                    if w.get(key):
                        w[key].Destroy()
                except Exception:
                    pass

        self.list.DeleteItem(row)
        self.row_order.pop(row)
        self.row_widgets.pop(uid, None)
        self._reindex_item_windows()
        self._schedule_progress_fit()

    def _column_width(self, col: int) -> int:
        """Ширина столбца: не меньше заданной по умолчанию и не меньше заголовка со стрелкой сортировки."""
        header = wx.ClientDC(self.list).GetTextExtent(self.COL_LABELS[col] + "  ▲").width
        return max(self.FromDIP(self.COL_DEFAULT_WIDTHS[col]), header + self.FromDIP(self.COL_HEADER_PADDING))

    def on_list_size(self, event):
        event.Skip()
        self._schedule_progress_fit()

    def _schedule_progress_fit(self):
        """Пересчёт ширины столбца прогресса после того, как список закончит раскладку."""
        wx.CallAfter(self._fit_progress_column)

    def _fit_progress_column(self):
        """
        Растягивает последний столбец на свободное место, чтобы не появлялась
        горизонтальная прокрутка. Ширины остальных столбцов заданы в DIP, но
        системный шрифт и полоса прокрутки на разных машинах занимают разное
        место, поэтому остаток считаем во время работы, а не на глаз.
        """
        try:
            try:
                available = self.list._mainWin.GetClientSize().width
            except AttributeError:
                available = self.list.GetClientSize().width - wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X)
            if available <= 0:
                return

            others = sum(
                self.list.GetColumnWidth(col)
                for col in range(self.list.GetColumnCount())
                if col != self.COL_PROGRESS and self.list.IsColumnShown(col)
            )
            width = max(available - others, self.FromDIP(self.COL_PROGRESS_MIN))
            if width != self.list.GetColumnWidth(self.COL_PROGRESS):
                self.list.SetColumnWidth(self.COL_PROGRESS, width)
        except RuntimeError:
            return  # окно уже уничтожено

    def _reindex_item_windows(self):
        """
        После DeleteItem UltimateListCtrl не пересчитывает _itemId у встроенных
        виджетов оставшихся строк. Из-за этого у сдвинувшихся строк остаётся
        устаревший _itemId (у последней — равный GetItemCount()), и при получении
        фокуса (например, открытии выпадающего списка аудио) OnSetFocus падает
        с «invalid item index in GetItemState».

        Чиним точечно: сопоставляем каждый встроенный виджет с его текущим
        индексом строки и обновляем _itemId у соответствующих элементов ULC.
        """
        try:
            items_with_window = self.list._mainWin._itemWithWindow
        except AttributeError:
            return

        widget_to_row: dict[int, int] = {}
        for row in range(self.list.GetItemCount()):
            widgets = self._widgets_at(row)
            if not widgets:
                continue
            for key in ("choice", "subtitles", "gauge"):
                ctrl = widgets.get(key)
                if ctrl is not None:
                    widget_to_row[id(ctrl)] = row

        for item in items_with_window:
            wnd = getattr(item, "_wnd", None)
            if wnd is not None and id(wnd) in widget_to_row:
                item._itemId = widget_to_row[id(wnd)]

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
            self.qp_slider.SetValue(self.global_settings.quality)
            self.qp_label.SetLabel(f"QP = {self.global_settings.quality}")
            self.qp_value = self.global_settings.quality
        else:
            self.slider_label.SetLabel("Битрейт (Мбит/с):")
            self.qp_slider.SetRange(2, 25)
            self.qp_slider.SetValue(self.global_settings.quality)
            self.qp_label.SetLabel(f"Битрейт = {self.global_settings.quality}")
            self.bitrate_value = self.global_settings.quality

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
            for row in range(self.list.GetItemCount()):
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
        self._schedule_progress_fit()
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

    def _selected_rows(self) -> list[int]:
        rows = []
        row = self.list.GetFirstSelected()
        while row != -1:
            rows.append(row)
            row = self.list.GetNextSelected(row)
        return rows

    # --- Прогноз размера ---
    def _global_settings_for_estimate(self) -> RowSettings:
        """
        Глобальные настройки для оценки. Пока строка выделена, панель показывает
        настройки этой строки, поэтому берём снимок, сделанный при выделении.
        """
        if self.global_settings and self.list.GetFirstSelected() != -1:
            return self.global_settings
        return self.get_current_settings()

    def update_row_estimate(self, row: int, global_settings: RowSettings | None = None):
        """Пересчитывает столбец «Ожид. размер» для одной строки."""
        widgets = self._widgets_at(row)
        if not widgets:
            return

        status = self.list.GetItem(row, self.COL_STATUS).GetText()
        # У конвертируемых и готовых строк там уже живой прогноз или точный размер.
        if "Конвертация" in status or "Готово" in status:
            return

        settings: RowSettings = widgets.get("settings") or RowSettings()
        if settings.is_global:
            settings = global_settings or self._global_settings_for_estimate()

        choice: wx.Choice | None = widgets.get("choice")
        audio_sel = choice.GetSelection() if choice else 0
        if audio_sel == wx.NOT_FOUND:
            audio_sel = 0

        est = estimate_output_size(widgets.get("info") or {}, settings, audio_sel, self.nvenc_available, widgets.get("probe"))
        widgets["est_bytes"] = int(est[0]) if est else 0
        widgets["est_text"] = format_estimate(est)
        self.list.SetStringItem(row, self.COL_EST, widgets["est_text"])

    def refresh_all_estimates(self):
        """Пересчитывает прогноз по всем строкам (только арифметика, без ffprobe)."""
        global_settings = self._global_settings_for_estimate()
        for row in range(self.list.GetItemCount()):
            self.update_row_estimate(row, global_settings)

    def _restore_row_estimate(self, row: int, widgets: dict, predicted_size: int):
        """Возвращает в столбец прогноз, посчитанный до старта: файл не готов."""
        widgets["est_bytes"] = predicted_size
        wx.CallAfter(self.list.SetStringItem, row, self.COL_EST, widgets.get("est_text") or "?")

    def on_audio_choice(self, event):
        # При «не конв. аудио» размер зависит от битрейта выбранной дорожки.
        event.Skip()
        self.refresh_all_estimates()

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
        video_info: dict | None = None,
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
        choice.Bind(wx.EVT_CHOICE, self.on_audio_choice)
        self.list.SetItemWindow(row, self.COL_AUDIO, choice, expand=True)

        gauge = wx.Gauge(self.list, range=100, size=self.FromDIP(wx.Size(-1, 18)), style=wx.GA_HORIZONTAL)
        gauge.SetValue(0)
        self.list.SetItemWindow(row, self.COL_PROGRESS, gauge, expand=True)

        uid = self._next_row_uid
        self._next_row_uid += 1
        self.row_order.append(uid)
        self.row_widgets[uid] = {
            "path": path,
            "choice": choice,
            "subtitles": None,
            "subtitle_tracks": subtitle_tracks,
            "gauge": gauge,
            "duration": float(duration or 0.0),
            "info": video_info or {},
            "settings": RowSettings(),
        }
        self.update_row_estimate(row)
        # Появившаяся вертикальная полоса прокрутки сужает список.
        self._schedule_progress_fit()
        if self.chk_save_subtitles.GetValue():
            self.create_subtitle_widget(row)
        if self.converting:
            # Строка добавлена во время конвертации: очередь подхватит её сама.
            self.all_jobs_duration += float(duration or 0.0)

    def create_subtitle_widget(self, row: int):
        widgets = self._widgets_at(row)
        if not widgets or widgets.get("subtitles"):
            return
        subtitle_choices = [track["display"] for track in widgets.get("subtitle_tracks", [])]
        subtitles = SubtitleCheckCombo(self.list, choices=subtitle_choices)
        self.list.SetItemWindow(row, self.COL_SUBTITLES, subtitles, expand=True)
        widgets["subtitles"] = subtitles

    def on_item_select(self, event):
        self.global_settings = self.get_current_settings()

    def on_close(self, event):
        if self.probing:
            self.cancel_probe()
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
        if self._instance_server is not None:
            self._instance_server.close()
        self.Destroy()

    def _conversion_locked_controls(self) -> list:
        """Элементы управления, которые должны блокироваться на время конвертации."""
        return [
            self.btn_remove,
            self.btn_clear,
            self.qp_slider,
            self.encode_mode,
            self.btn_save_folder_browse,
            self.btn_clear_save_folder,
            self.toggle_suffix,
            self.chk_save_subtitles,
            self.slider_label,
            self.chk_limit_res,
            self.tonemapping_label,
            self.choice_tonemap,
            self.chk_skip_video,
            self.chk_skip_audio,
            self.chk_copy_tags,
        ]

    @staticmethod
    def _set_row_widgets_enabled(widgets: dict, enabled: bool):
        """Блокирует/разблокирует виджеты выбора дорожек одной строки."""
        for key in ("choice", "subtitles"):
            ctrl = widgets.get(key)
            if ctrl:
                ctrl.Enable(enabled)

    def _set_rows_enabled(self, enabled: bool):
        """Блокирует/разблокирует виджеты выбора дорожек во всех строках списка."""
        for widgets in self.row_widgets.values():
            self._set_row_widgets_enabled(widgets, enabled)

    def disable_interface(self):
        # Строки не блокируем: у ожидающих строк дорожки можно менять до их старта,
        # очередь читает выбор в момент начала конвертации строки.
        for ctrl in self._conversion_locked_controls():
            ctrl.Disable()

    def enable_interface(self):
        for ctrl in self._conversion_locked_controls():
            ctrl.Enable()
        self._set_rows_enabled(True)
        # Восстанавливаем зависимость: при «не конв. видео» видео-настройки выключены.
        if self.chk_skip_video.GetValue():
            for ctrl in (
                self.chk_limit_res,
                self.tonemapping_label,
                self.choice_tonemap,
                self.slider_label,
                self.qp_slider,
                self.encode_mode,
            ):
                ctrl.Disable()

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

    def get_current_settings(self) -> RowSettings:
        return RowSettings(
            is_global=False,
            encode_mode=self.encode_mode.GetSelection(),
            quality=self.qp_slider.GetValue(),
            limit_res=self.chk_limit_res.GetValue(),
            tonemapping=self.choice_tonemap.GetSelection(),
            skip_video=self.chk_skip_video.GetValue(),
            skip_audio=self.chk_skip_audio.GetValue(),
        )

    def reset_global_settings(self):
        if self.global_settings:
            self.encode_mode.SetSelection(self.global_settings.encode_mode)
            self.qp_slider.SetValue(self.global_settings.quality)
            self.on_mode_and_qp_reset()
            self.chk_limit_res.SetValue(self.global_settings.limit_res)
            self.choice_tonemap.SetSelection(self.global_settings.tonemapping)
            self.chk_skip_video.SetValue(self.global_settings.skip_video)
            if self.global_settings.skip_video:
                self.on_skip_video(None)
            self.chk_skip_audio.SetValue(self.global_settings.skip_audio)
        self.refresh_all_estimates()

    def save_settings_to_sel_rows_and_update_list(self):
        item_index = self.list.GetFirstSelected()
        while item_index != -1:
            settings = self.get_current_settings()
            widgets = self._widgets_at(item_index)
            if widgets:
                widgets["settings"] = settings
            self.list.SetStringItem(item_index, self.COL_SETTINGS, format_row_settings(settings))
            self.list.SetItemBackgroundColour(item_index, wx.Colour(255, 251, 235))
            self.list.Refresh()
            item_index = self.list.GetNextSelected(item_index)
        self.refresh_all_estimates()

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
        widgets = self._widgets_at(item_index)
        if not widgets:
            return
        widgets["settings"] = RowSettings()
        self.list.SetStringItem(item_index, self.COL_SETTINGS, "⚙️Глобальные")
        self.list.SetItemBackgroundColour(item_index, wx.Colour(255, 255, 255))
        self.list.Refresh()
        self.refresh_all_estimates()

    def on_info_page(self, event):
        description = """\
Программа для быстрого перекодирования видео в формат MP4 с использованием аппаратного ускорителя видеокарт NVIDIA. Основана на FFmpeg.

Доступны следующие форматы входных файлов: MKV, MP4, MOV, AVI.
Выходной формат: MP4.
Видеокодек: NVENC (H.264), аудиокодек: AAC.
Настройки качества: режим постоянного качества (QP) или режим постоянного битрейта (CBR).
Для аппаратного ускорения нужна видеокарта NVIDIA с поддержкой NVENC. Если NVENC недоступен, используется программное кодирование на CPU (libx264)."""
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
