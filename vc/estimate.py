"""Прогноз размера выходного файла."""
from vc.ffmpeg_tools import get_audio_bitrate_kbps
from vc.settings import RowSettings
from vc.utils import human_size, to_int
# --- Прогноз размера выходного файла ---
# Константы откалиброваны на реальных файлах (8 источников 1080p/720p, QP 16–32,
# см. calibrate_estimate.py): фрагменты кодировались точно теми же аргументами
# h264_nvenc, что и в _build_video_args, и сравнивались с полными конвертациями.
#
# Главный вывод калибровки: при одном и том же QP битрейт NVENC отличается втрое
# в зависимости от содержимого (чистый WEB-DL ≈ 0.09 бит/пиксель, зернистый
# BluRay ≈ 0.28), поэтому модель «бит на пиксель» сама по себе даёт ошибку до
# 2.5×. Битрейт источника — хороший признак сложности картинки: на QP=22 выход
# NVENC обычно составляет 0.75–1.25 от него. Итоговая оценка — геометрическое
# смешение двух моделей.
#
# Бит на пиксель (за секунду) для h264_nvenc при QP=22, типичное значение.
# Используется как «якорь» смешения и как единственная модель, когда битрейт
# источника неизвестен.
EST_BITS_PER_PIXEL_QP22 = 0.15
# Вес битрейта источника в смешении (0 — только модель по пикселям, 1 — только
# источник).
EST_SOURCE_WEIGHT = 0.6
# Битрейт источника учитывается только в пределах правдоподобного диапазона
# бит/пиксель: слишком пережатый источник (0.8 Мбит/с на 1920×800) после
# перекодирования вырастает в 5–6 раз, слишком «толстый» — сильно ужимается.
EST_SOURCE_BPP_MIN = 0.10
EST_SOURCE_BPP_MAX = 0.28
# Шаг QP, при котором битрейт NVENC меняется вдвое (измерено 5.2–6.2).
EST_QP_HALVING_STEP = 5.5
# Потолок битрейта NVENC в режиме -rc vbr -b:v 0: на зернистых источниках
# 1080p при QP 16–19 выход упирается в ≈16 Мбит/с независимо от QP. Для более
# крупных кадров потолок масштабируем пропорционально числу пикселей.
EST_NVENC_CEILING_BPS = 16_000_000
EST_NVENC_CEILING_PIXEL_RATE = 1920 * 1080 * 24
# libx264 с тем же числовым значением (CRF) даёт примерно вдвое меньший битрейт,
# чем NVENC с -cq.
EST_NVENC_BITRATE_FACTOR = 1.9
# Накладные расходы контейнера MP4.
EST_CONTAINER_OVERHEAD = 1.01
# Контрольная кодировка: фрагменты (доли длительности) и длина каждого, с.
# Шесть фрагментов по 10 с, равномерно по файлу: на трёх полных кодировках
# (сериал и фильм, QP 22 и 30) ошибка не превышала 6%, тогда как три фрагмента
# по 20 с давали до 9%. Более короткие фрагменты завышают битрейт из-за
# «разгона» энкодера после ключевого кадра.
PROBE_POSITIONS = tuple((i + 0.5) / 6 for i in range(6))
PROBE_SEGMENT_SEC = 10


def source_video_bitrate_bps(info: dict) -> int:
    """
    Битрейт видеопотока источника в бит/с. Если ffprobe не отдал его напрямую
    (типично для MKV), вычитаем аудио из общего битрейта контейнера, а в крайнем
    случае считаем общий битрейт из размера файла и длительности.
    """
    own = to_int(info.get("video_bitrate_bps"))
    if own > 0:
        return own

    audio_total = sum(to_int(a.get("bit_rate")) for a in (info.get("audio_streams") or []))

    total = to_int(info.get("format_bitrate_bps"))
    if total <= 0:
        duration = float(info.get("duration") or 0.0)
        size = to_int(info.get("size"))
        if duration > 0 and size > 0:
            total = int(size * 8 / duration)

    return max(total - audio_total, 0)


def estimate_qp_video_bitrate_bps(info: dict, qp: int, limit_res: bool, nvenc: bool = False) -> int:
    """
    Грубая оценка битрейта видео в режиме постоянного качества.

    Базовый битрейт на QP=22 — геометрическое смешение модели «бит на пиксель»
    и битрейта источника (ограниченного правдоподобным диапазоном бит/пиксель);
    дальше он удваивается/делится пополам каждые EST_QP_HALVING_STEP единиц QP
    и упирается в потолок NVENC. Для libx264 результат делится на
    EST_NVENC_BITRATE_FACTOR. Возвращает 0, если разрешение неизвестно.
    """
    w = to_int(info.get("width"))
    h = to_int(info.get("height"))
    if w <= 0 or h <= 0:
        return 0

    try:
        fps = float(info.get("fps"))
    except (TypeError, ValueError):
        fps = 0.0
    if fps <= 0:
        fps = 25.0

    src_pixel_rate = w * h * fps
    # Тот же порог понижения разрешения, что и в _build_video_args.
    if limit_res and (w > 1920 or h > 1080):
        scale = min(1920 / w, 1080 / h, 1.0)
        w = int(w * scale)
        h = int(h * scale)
    pixel_rate = w * h * fps

    pixel_model = EST_BITS_PER_PIXEL_QP22 * pixel_rate

    src = source_video_bitrate_bps(info)
    if src > 0:
        # При понижении разрешения битрейт источника пересчитываем на новое число пикселей.
        src = src * pixel_rate / src_pixel_rate
        src = min(max(src, EST_SOURCE_BPP_MIN * pixel_rate), EST_SOURCE_BPP_MAX * pixel_rate)
        base = src**EST_SOURCE_WEIGHT * pixel_model ** (1.0 - EST_SOURCE_WEIGHT)
    else:
        base = pixel_model

    bps = base * (2 ** ((22 - to_int(qp, 22)) / EST_QP_HALVING_STEP))
    bps = min(bps, EST_NVENC_CEILING_BPS * max(1.0, pixel_rate / EST_NVENC_CEILING_PIXEL_RATE))
    if not nvenc:
        bps /= EST_NVENC_BITRATE_FACTOR

    return int(bps)


def probe_video_bitrate_bps(probe: dict | None, eff: RowSettings, nvenc: bool) -> int:
    """
    Битрейт видео по результату контрольной кодировки (см. probe_worker) для
    действующих настроек. Замер сделан при конкретном QP; при другом QP он
    пересчитывается по шагу EST_QP_HALVING_STEP. Если изменились разрешение,
    tonemapping или энкодер, замер не годится — возвращает 0.
    """
    if not probe or to_int(probe.get("video_bps")) <= 0:
        return 0
    if bool(probe.get("limit_res")) != bool(eff.limit_res) or to_int(probe.get("tonemapping")) != to_int(eff.tonemapping):
        return 0
    if bool(probe.get("nvenc")) != bool(nvenc):
        return 0
    qp_delta = to_int(probe.get("qp"), 22) - to_int(eff.quality, 22)
    return int(to_int(probe["video_bps"]) * (2 ** (qp_delta / EST_QP_HALVING_STEP)))


def estimate_output_size(
    info: dict,
    eff: RowSettings,
    audio_tracks: list[int],
    nvenc: bool = False,
    probe: dict | None = None,
) -> tuple[int, bool] | None:
    """
    Ожидаемый размер выходного файла в байтах. audio_tracks — индексы (a:N)
    аудиодорожек, попадающих в выходной файл; пустой список — без аудио.
    Возвращает (байты, грубая_оценка) либо None, если данных не хватает.
    Оценка точная для CBR, для «не конв. видео» и для режима QP, если есть
    контрольная кодировка при том же QP; в остальных случаях для QP — грубая.
    """
    duration = float(info.get("duration") or 0.0)
    if duration <= 0:
        return None

    audio_streams = info.get("audio_streams") or []
    audio_bps = 0
    for sel in dict.fromkeys(audio_tracks):
        track = audio_streams[sel] if 0 <= sel < len(audio_streams) else {}
        channels = to_int(track.get("channels")) or 2
        if eff.skip_audio:
            audio_bps += to_int(track.get("bit_rate")) or get_audio_bitrate_kbps(channels) * 1000
        else:
            audio_bps += get_audio_bitrate_kbps(channels) * 1000

    rough = False
    if eff.skip_video:
        video_bps = source_video_bitrate_bps(info)
    elif eff.encode_mode == 1:
        video_bps = to_int(eff.quality) * 1_000_000
    else:
        video_bps = probe_video_bitrate_bps(probe, eff, nvenc)
        if video_bps > 0:
            # Замер при другом QP пересчитан по модели — уже не точный.
            rough = to_int(probe.get("qp"), 22) != to_int(eff.quality, 22)
        else:
            video_bps = estimate_qp_video_bitrate_bps(info, eff.quality, eff.limit_res, nvenc)
            rough = True

    if video_bps <= 0:
        return None

    return int((video_bps + audio_bps) * duration / 8 * EST_CONTAINER_OVERHEAD), rough


def format_estimate(est: tuple[int, bool] | None) -> str:
    """«~ 1.2 GB» для грубой оценки, «≈ 1.2 GB» для расчётной, «?» если оценки нет."""
    if not est:
        return "?"
    size, rough = est
    return f"{'~' if rough else '≈'} {human_size(size)}"
