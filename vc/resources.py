"""Пути к ресурсам приложения (ffmpeg, ffprobe, mpv, иконки, лицензия)."""
import os
import sys


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


def read_from_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
