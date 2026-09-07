"""
Калибровка прогноза размера (estimate_qp_video_bitrate_bps в main.py).

Из каждого файла вырезаются несколько фрагментов, кодируются теми же
аргументами h264_nvenc, что и в приложении, при нескольких значениях QP,
и измеренный битрейт сравнивается с прогнозом текущей модели.

Использование:
    python calibrate_estimate.py <файл или папка> [...]
    python calibrate_estimate.py --report          # только отчёт по calibrate_estimate.json

Результаты накапливаются в calibrate_estimate.json рядом со скриптом.
"""

import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = os.path.join(HERE, "ffmpeg.exe")
FFPROBE = os.path.join(HERE, "ffprobe.exe")
RESULTS = os.path.join(HERE, "calibrate_estimate.json")

QPS = [16, 19, 22, 25, 28, 32]
SEGMENT_SEC = 30
POSITIONS = [0.2, 0.5, 0.8]
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".webm", ".wmv"}


def probe(path: str) -> dict:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,bit_rate",
         "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return json.loads(r.stdout or "{}")


def nvenc_args(qp: int) -> list[str]:
    # Должно совпадать с _build_video_args в main.py.
    return [
        "-pix_fmt", "yuv420p", "-vf", "format=yuv420p",
        "-c:v", "h264_nvenc", "-preset", "p4",
        "-rc", "vbr", "-cq", str(qp), "-b:v", "0", "-qmin", "0",
        "-profile:v", "high", "-tune", "hq", "-b_ref_mode", "middle", "-spatial_aq", "1",
    ]


def encode_segment(path: str, start: float, qp: int, out: str) -> tuple[int, float]:
    """Возвращает (байты видео, секунды) закодированного фрагмента."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.2f}", "-t", str(SEGMENT_SEC), "-i", path,
           "-map", "0:v:0", "-an", "-sn", *nvenc_args(qp), out]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  ошибка ffmpeg (qp={qp}, start={start:.0f}): {r.stderr.strip()[-300:]}")
        return 0, 0.0
    info = probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    dur = float(info["format"]["duration"])
    return int(int(v["bit_rate"]) * dur / 8), dur


def calibrate(path: str) -> dict | None:
    info = probe(path)
    if not info.get("streams"):
        print(f"пропуск (ffprobe не прочитал): {path}")
        return None
    try:
        v = next(s for s in info["streams"] if s["codec_type"] == "video" and int(s.get("width") or 0) > 400)
    except StopIteration:
        print(f"пропуск (нет видеопотока): {path}")
        return None
    dur = float(info["format"]["duration"])
    num, den = v["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    audio_bps = sum(int(s.get("bit_rate") or 0) for s in info["streams"] if s["codec_type"] == "audio")
    fmt_bps = int(info["format"].get("bit_rate") or 0)
    src_v = int(v.get("bit_rate") or 0) or max(fmt_bps - audio_bps, 0)

    rec = {
        "file": os.path.basename(path), "codec": v.get("codec_name"),
        "w": int(v["width"]), "h": int(v["height"]), "fps": fps, "dur": dur,
        "src_video_bps": src_v, "qp": {},
    }
    print(f"{rec['file']}  {rec['w']}x{rec['h']} {fps:.2f} fps, {rec['codec']}, источник {src_v / 1e6:.2f} Мбит/с")
    with tempfile.TemporaryDirectory() as tmp:
        for qp in QPS:
            total_bytes, total_sec = 0, 0.0
            for pos in POSITIONS:
                b, s = encode_segment(path, dur * pos, qp, os.path.join(tmp, "seg.mp4"))
                total_bytes += b
                total_sec += s
            bps = int(total_bytes * 8 / total_sec) if total_sec else 0
            rec["qp"][str(qp)] = bps
            print(f"  qp={qp:2d} -> {bps / 1e6:6.2f} Мбит/с")
    return rec


def report(records: list[dict]) -> None:
    sys.argv = sys.argv[:1]
    from main import estimate_qp_video_bitrate_bps

    errs, errs22 = [], []
    print(f"\n{'файл':40s} qp: прогноз/факт, Мбит/с")
    for r in records:
        info = {"width": r["w"], "height": r["h"], "fps": r["fps"], "video_bitrate_bps": r["src_video_bps"]}
        cells = []
        for qp, actual in sorted((int(k), v) for k, v in r["qp"].items()):
            if actual <= 0:
                continue
            pred = estimate_qp_video_bitrate_bps(info, qp, True, True)
            e = abs(math.log(pred / actual))
            errs.append(e)
            if qp == 22:
                errs22.append(e)
            cells.append(f"{qp}: {pred / 1e6:5.2f}/{actual / 1e6:5.2f}")
        print(f"{r['file'][:40]:40s} " + "  ".join(cells))
    if errs:
        mean = math.exp(sum(errs) / len(errs)) - 1
        mx = math.exp(max(errs)) - 1
        m22 = math.exp(sum(errs22) / len(errs22)) - 1 if errs22 else 0
        print(f"\nсредняя ошибка {mean:.1%}, максимальная {mx:.1%}, на QP=22 средняя {m22:.1%}")


def main() -> None:
    records = []
    if os.path.exists(RESULTS):
        records = json.load(open(RESULTS, encoding="utf-8"))

    args = [a for a in sys.argv[1:] if a != "--report"]
    paths = []
    for a in args:
        if os.path.isdir(a):
            paths += [os.path.join(a, f) for f in sorted(os.listdir(a))
                      if os.path.splitext(f)[1].lower() in VIDEO_EXT]
        elif os.path.isfile(a):
            paths.append(a)
        else:
            print(f"не найдено: {a}")

    if not paths and "--report" not in sys.argv:
        print(__doc__)
        return

    known = {r["file"] for r in records}
    for p in paths:
        if os.path.basename(p) in known:
            print(f"уже есть в результатах, пропуск: {os.path.basename(p)}")
            continue
        rec = calibrate(p)
        if rec:
            records.append(rec)
            json.dump(records, open(RESULTS, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    if records:
        report(records)
    else:
        print("нет результатов")


if __name__ == "__main__":
    main()
