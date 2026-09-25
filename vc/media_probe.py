"""Анализ медиафайлов через ffprobe: дорожки, HDR, сводка о видео."""
import json
import subprocess

from vc.resources import FFPROBE_PATH
from vc.utils import fix_text_encoding, to_int


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


def probe_media(filepath: str) -> dict:
    """
    Один вызов ffprobe со всеми потоками и форматом.
    Возвращает полный JSON (или {}); результат можно разобрать
    функциями parse_* без повторных запусков ffprobe.
    """
    return run_ffprobe_json([
        FFPROBE_PATH,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        filepath,
    ])


def streams_of_type(probe: dict, codec_type: str) -> list[dict]:
    """Потоки заданного типа (audio/video/subtitle) в порядке контейнера."""
    return [s for s in (probe.get("streams") or []) if s.get("codec_type") == codec_type]


def parse_audio_tracks(probe: dict) -> list[str]:
    """
    Возвращает список строк для Choice по разобранному ffprobe-JSON.
    Важно: stream.index у ffprobe — это индекс потока в контейнере (может быть 1,2,3...),
    а выбор у пользователя будет 0..N-1 (порядок аудио-стримов).
    Мы показываем stream.index в тексте, но мапим по порядку (a:0, a:1...).
    """
    tracks: list[str] = []
    for stream in streams_of_type(probe, "audio"):
        idx = stream.get("index", "?")
        codec = stream.get("codec_name", "?")
        ch = stream.get("channels", "?")
        br = stream.get("bit_rate")
        tags = stream.get("tags", {}) or {}

        lang = tags.get("language", "und")
        title_raw = (tags.get("title") or "").strip()
        title = fix_text_encoding(title_raw)

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


def get_audio_tracks(filepath: str) -> list[str]:
    return parse_audio_tracks(probe_media(filepath))


def parse_subtitle_tracks(probe: dict) -> list[dict]:
    """
    Возвращает субтитры в порядке s:0, s:1... по разобранному ffprobe-JSON.
    Для MP4 сохраняем только текстовые дорожки, которые ffmpeg умеет
    перекодировать в mov_text.
    """
    text_codecs = {
        "subrip",
        "ass",
        "ssa",
        "webvtt",
        "mov_text",
        "text",
    }

    tracks: list[dict] = []
    for subtitle_order, stream in enumerate(streams_of_type(probe, "subtitle")):
        idx = stream.get("index", "?")
        codec = stream.get("codec_name", "?")
        tags = stream.get("tags", {}) or {}
        lang = tags.get("language", "und")
        title_raw = (tags.get("title") or "").strip()
        title = fix_text_encoding(title_raw)
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


def get_subtitle_tracks(filepath: str) -> list[dict]:
    return parse_subtitle_tracks(probe_media(filepath))


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


def parse_hdr_info(probe: dict) -> dict:
    """
    Упрощённый HDR анализ по первому видеопотоку из разобранного ffprobe-JSON.
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

    streams = streams_of_type(probe, "video")
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


def get_hdr_info(file_path: str) -> dict:
    return parse_hdr_info(probe_media(file_path))


def parse_video_info(probe: dict) -> dict:
    """Сводная информация о первом видеопотоке и контейнере из разобранного ffprobe-JSON."""
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
        # Числовые поля для прогноза размера (bitrate выше — только для показа).
        "video_bitrate_bps": 0,
        "format_bitrate_bps": 0,
        "audio_streams": [],
    }

    videos = streams_of_type(probe, "video")
    stream = videos[0] if videos else {}
    fmt = probe.get("format") or {}

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

    info["video_bitrate_bps"] = to_int(stream.get("bit_rate"))
    info["format_bitrate_bps"] = to_int(fmt.get("bit_rate"))
    # Порядок совпадает с порядком дорожек в wx.Choice (см. parse_audio_tracks).
    info["audio_streams"] = [
        {
            "channels": to_int(s.get("channels")),
            "bit_rate": to_int(s.get("bit_rate")),
            "language": (s.get("tags", {}) or {}).get("language", "und"),
            "title": fix_text_encoding(((s.get("tags", {}) or {}).get("title") or "").strip()),
        }
        for s in streams_of_type(probe, "audio")
    ]

    # duration
    try:
        info["duration"] = float(fmt.get("duration") or 0.0)
    except Exception:
        info["duration"] = 0.0

    hdr = parse_hdr_info(probe)
    info["hdr_type"] = hdr["type"]
    info["requires_tonemap"] = bool(hdr["requires_tonemap"])

    return info


def get_video_info(filepath: str) -> dict:
    return parse_video_info(probe_media(filepath))
