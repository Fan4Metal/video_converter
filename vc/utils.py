"""Небольшие вспомогательные функции без зависимости от wx."""
import os

from mutagen.mp4 import MP4


def copy_mp4_tags(source_path: str, dest_path: str) -> tuple[bool, str]:
    """
    Копирует MP4-теги из исходного файла в выходной.
    Возвращает (успех, текст_ошибки); текст ошибки пустой при успехе.
    """
    try:
        video = MP4(source_path)
        new_video = MP4(dest_path)
        for tag in video.tags:
            new_video.tags[tag] = video.tags[tag]
        new_video.save()
        return True, ""
    except Exception as e:
        return False, str(e)


def fix_text_encoding(text: str) -> str:
    """
    Чинит частый случай mojibake: текст в UTF-8, ошибочно прочитанный как cp1251.
    Возвращает исходную строку, если перекодировка не нужна или невозможна.
    """
    try:
        repaired = text.encode("cp1251").decode("utf-8")
    except Exception:
        return text
    if any(marker in text for marker in ("Ð", "Ñ", "Â", "Ã")) and repaired:
        return repaired
    return text


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def to_int(value, default: int = 0) -> int:
    """Безопасное приведение к int (ffprobe часто отдаёт числа строками или None)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


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
