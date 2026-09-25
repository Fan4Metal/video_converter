"""Проверка FFmpeg/NVENC и параметры кодирования аудио."""
import re
import subprocess

from vc.utils import to_int


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


def check_nvenc_available(ffmpeg_path: str) -> bool:
    """
    Проверяет, доступен ли аппаратный энкодер NVIDIA NVENC (h264_nvenc).
    Делает короткий тестовый прогон на синтетическом источнике: если энкодер
    отсутствует или нет совместимой видеокарты, ffmpeg вернёт ненулевой код.
    """
    try:
        result = subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-f",
                "lavfi",
                "-i",
                "nullsrc=s=256x256:d=0.1",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        return False


# --- Определение битрейта по количеству каналов ---
def get_audio_bitrate_kbps(channels: int) -> int:
    """Битрейт AAC-дорожки в кбит/с по количеству каналов."""
    ch = to_int(channels, 2)
    if ch <= 1:
        return 128
    if ch == 2:
        return 192
    if ch <= 6:
        return 384
    if ch >= 8:
        return 512
    return 256


def get_audio_bitrate(channels: int) -> str:
    return f"{get_audio_bitrate_kbps(channels)}k"
