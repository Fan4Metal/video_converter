"""Настройки кодирования строки списка."""

from dataclasses import dataclass


@dataclass
class RowSettings:
    """
    Настройки кодирования строки.

    is_global=True означает «использовать текущие настройки панели управления»;
    в этом случае остальные поля игнорируются. Поле quality — значение слайдера:
    QP (encode_mode=0) или битрейт в Мбит/с (encode_mode=1).
    """

    is_global: bool = True
    encode_mode: int = 0
    quality: int = 22
    limit_res: bool = False
    tonemapping: int = 0
    skip_video: bool = False
    skip_audio: bool = False


def format_row_settings(settings: RowSettings) -> str:
    """Короткая строка для столбца «Параметры»."""
    if settings.skip_video:
        video_str = "В: не конв."
    else:
        if settings.encode_mode == 0:
            video_str = f"QP={settings.quality}"
        else:
            video_str = f"CBR={settings.quality}"
        if settings.limit_res:
            video_str += ", fullHD"
        tm_string = settings.tonemapping
        if tm_string == 2:
            video_str += ", TM=выкл"
        elif tm_string == 1:
            video_str += ", TM=вкл"
        elif tm_string == 0:
            video_str += ", TM=auto"

    if settings.skip_audio:
        audio_str = ", А: не конв."
    else:
        audio_str = ""
    return f"{video_str}{audio_str}"
