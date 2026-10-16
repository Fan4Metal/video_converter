"""Настройки кодирования строки списка."""

from dataclasses import dataclass

# Ограничения разрешения: высота рамки -> (длинная сторона, короткая сторона).
RESOLUTION_LIMITS = {
    1080: (1920, 1080),
    720: (1280, 720),
    576: (1024, 576),
}
# Пункты выпадающего списка «Разрешение»: (max_res, подпись).
RESOLUTION_CHOICES = [
    (0, "Не менять"),
    (1080, "До Full HD (1080p)"),
    (720, "До HD (720p)"),
    (576, "До SD (576p)"),
]


def target_resolution(width: int, height: int, max_res: int) -> tuple[int, int]:
    """
    Размер кадра после ограничения разрешения. Кадр вписывается в рамку
    RESOLUTION_LIMITS[max_res] с сохранением пропорций; для вертикального кадра
    рамка поворачивается (1080p — короткая сторона ≤1080, длинная ≤1920).
    Кадр, который уже помещается, не меняется (не увеличивается).

    Повторяет расчёт фильтра scale из resolution_filter (force_original_aspect_ratio=
    decrease с force_divisible_by=2), чтобы показанный размер совпадал с размером
    выходного файла.
    """
    limit = RESOLUTION_LIMITS.get(max_res)
    if not limit or width <= 0 or height <= 0:
        return width, height
    long_side, short_side = limit
    box_w, box_h = (long_side, short_side) if width >= height else (short_side, long_side)
    if width <= box_w and height <= box_h:
        return width, height
    w, h = min(width, box_w), min(height, box_h)
    # Подогнанная по пропорциям сторона округляется до ближайшего чётного.
    fit_w = (h * width + height) // (2 * height) * 2
    fit_h = (w * height + width) // (2 * width) * 2
    w, h = min(fit_w, w), min(fit_h, h)
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


def resolution_filter(max_res: int) -> str:
    """
    Фильтр ffmpeg для ограничения разрешения (см. target_resolution). Рамка
    выбирается по iw/ih самого кадра, потому что ffmpeg уже повернул его по
    метке поворота и размеры из ffprobe могут быть переставлены.
    """
    long_side, short_side = RESOLUTION_LIMITS[max_res]
    return (
        f"scale=w='if(gte(iw,ih),min(iw,{long_side}),min(iw,{short_side}))'"
        f":h='if(gte(iw,ih),min(ih,{short_side}),min(ih,{long_side}))'"
        ":force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos"
    )


@dataclass
class RowSettings:
    """
    Настройки кодирования строки.

    is_global=True означает «использовать текущие настройки панели управления»;
    в этом случае остальные поля игнорируются. Поле quality — значение слайдера:
    QP (encode_mode=0) или битрейт в Мбит/с (encode_mode=1). Поле max_res —
    ключ RESOLUTION_LIMITS (0 — разрешение не менять).
    """

    is_global: bool = True
    encode_mode: int = 0
    quality: int = 22
    max_res: int = 0
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
        if settings.max_res in RESOLUTION_LIMITS:
            video_str += f", ≤{settings.max_res}p"
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
