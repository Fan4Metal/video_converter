"""Строка списка файлов: данные файла, встроенные виджеты и состояние очереди."""
from dataclasses import dataclass, field
from typing import Literal

import wx

from vc.settings import RowSettings
from vc.widgets import CheckListCombo

# Необязательные столбцы выбора дорожек: виджеты есть, только пока столбец показан.
TrackColumn = Literal["audio_multi", "subtitles"]


@dataclass(eq=False, slots=True)
class FileRow:
    """
    Одна строка списка. Хранится в VideoConverter.entries по стабильному uid
    (индекс строки меняется при удалении и сортировке). slots: опечатка в имени
    поля — сразу AttributeError, а не новый молча заведённый атрибут.
    """
    path: str
    # Данные ffprobe: дорожки и сведения о видео (parse_video_info)
    audio_choices: list[str]
    subtitle_tracks: list[dict]
    duration: float
    info: dict
    # Встроенные виджеты строки. Столбцы «несколько аудио» и «субтитры»
    # создаются, только пока они показаны.
    choice: wx.Choice
    gauge: wx.Gauge
    audio_multi: CheckListCombo | None = None
    subtitles: CheckListCombo | None = None
    # Настройки кодирования строки (is_global — берутся с панели)
    settings: RowSettings = field(default_factory=RowSettings)
    # Прогноз размера: байты и текст столбца; во время и после кодирования —
    # живой прогноз и фактический размер.
    est_bytes: int = 0
    est_text: str = ""
    # Замер контрольной кодировки (см. ProbeMixin), None — замера нет
    probe: dict | None = None
    # Очередь: строка помечена на пропуск; готовый выходной файл
    skip: bool = False
    output_file: str | None = None

    def embedded_windows(self) -> list[wx.Window]:
        """Встроенные виджеты строки, которые существуют сейчас."""
        return [w for w in (self.choice, self.audio_multi, self.subtitles, self.gauge) if w is not None]

    def destroy_windows(self):
        """Уничтожает встроенные виджеты (перед удалением строки или пересборкой списка)."""
        for window in self.embedded_windows():
            try:
                window.Destroy()
            except RuntimeError:
                pass  # уже уничтожен вместе со списком
        self.audio_multi = None
        self.subtitles = None

    def destroy_track_widget(self, attr: TrackColumn):
        """Уничтожает виджет необязательного столбца дорожек (столбец скрыт)."""
        ctrl = getattr(self, attr)
        if ctrl is not None:
            try:
                ctrl.Destroy()
            except RuntimeError:
                pass
        setattr(self, attr, None)

    def set_tracks_enabled(self, enabled: bool):
        """Блокирует/разблокирует виджеты выбора дорожек."""
        for ctrl in (self.choice, self.audio_multi, self.subtitles):
            if ctrl is not None:
                ctrl.Enable(enabled)
