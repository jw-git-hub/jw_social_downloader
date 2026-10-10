"""Перекодирование видео в H.264 для клиентов Telegram, не умеющих VP9/AV1.

Instagram отдаёт лучшее разрешение (1080p) только в VP9; iPhone-клиент
Telegram VP9 не показывает — картинка стоит на первом кадре, звук идёт.
Решение о перекодировании принимается по факту: кодек готового файла берётся
из ffprobe (`bot/services/media_probe.py`), а не угадывается по списку форматов.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

from loguru import logger

from bot.services.media_probe import MediaInfo
from bot.services.ytdlp_progress import MIB

H264_CODEC_NAME = "h264"
AAC_CODEC_NAME = "aac"
# Замер 2026-10-10 на RK3399 в prod-образе (libx264 veryfast crf 20, 1080x1920
# 30 fps, VP9-источник): 8,4 с видео кодируются за ~20 с, то есть ~2,4 с на
# секунду видео при загруженном хосте. 90 с ролика — это ~3,6 минуты работы.
# Ролики длиннее не перекодируем, а качаем в H.264 пониженного разрешения.
TRANSCODE_MAX_DURATION_SEC = 90
TRANSCODE_SECONDS_PER_VIDEO_SECOND = 2.5  # с запасом к замеру, для оценки «осталось» в статусе
# Тонмаппинг HDR (zscale+tonemap на float-кадрах) втрое медленнее обычного
# перекодирования. Замер 2026-10-10 в jw_downloader:followups-test, 1080x1920 30 fps:
# 5,9 с на секунду видео (обычное — 1,4–1,7); ревьюер на том же железе: 5,9–6,1.
HDR_SECONDS_PER_VIDEO_SECOND = 6.5
# Бюджет времени работы ffmpeg на пост (~3,75 мин): прежние 90 с обычной скорости.
# С ним сравниваем оценку времени, а не голую длительность, — иначе HDR на 70–90 с
# уходил бы в таймаут вместо повторной загрузки в H.264.
TRANSCODE_MAX_WORK_SEC = TRANSCODE_MAX_DURATION_SEC * TRANSCODE_SECONDS_PER_VIDEO_SECOND
TRANSCODE_TIMEOUT_SEC = 600  # отсчитывается после захвата очереди, а не вместе с ожиданием
# Загрузки идут под общим Semaphore(3), а libx264 и один занимает все 6 ядер:
# три параллельных перекодирования по 80–90 с вылетели бы в таймаут. Поэтому
# перекодируем по одному, остальные ждут своей очереди.
MAX_CONCURRENT_TRANSCODES = 1
NICE_PRIORITY = "10"  # перекодирование не должно душить бота и остальные загрузки
# crf 20 / veryfast: на VP9-источнике визуально без потерь (источник уже сжат
# сильнее), файл ~3x больше источника; crf 18 давал ещё +27 % размера за то же время.
X264_PRESET = "veryfast"
X264_CRF = "20"
OUTPUT_PIXEL_FORMAT = "yuv420p"
# libx264 + yuv420p не принимают нечётную ширину/высоту; pad добавляет один
# пиксель вместо того, чтобы отрезать или сжимать кадр.
EVEN_SIZE_PAD_FILTER = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
AAC_FALLBACK_BITRATE = "192k"
# HDR (PQ / HLG) через `-pix_fmt yuv420p` без тонмаппинга даёт блёклую картинку.
# Теги источника не всегда проставлены (контейнер mp4 с vp09 их нередко теряет),
# поэтому `setparams` задаёт их явно: PQ и HLG бывают только в BT.2020, limited range.
HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})
HDR_TO_SDR_FILTER = (
    "setparams=color_trc={transfer}:colorspace=bt2020nc:color_primaries=bt2020:range=tv,"
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
SDR_COLOR_TAG_ARGS = ("-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709")
TRANSCODED_SUFFIX = "_h264"
OUTPUT_EXTENSION = ".mp4"
STDERR_LOG_LIMIT_CHARS = 300


# Размер перекодированного файла к источнику (замер: VP9 2,06 МБ → H.264 5,92 МБ);
# нужен только для строки статуса «Готовлю видео…».
TRANSCODE_OUTPUT_SIZE_RATIO = 3.0

# Семафор создаётся лениво под текущий event loop: asyncio.Semaphore привязывается
# к циклу при первом ожидании, а циклов в тестах несколько.
_slots_by_loop: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}
_backlog_sec = 0.0  # оценка работы всех перекодирований, вставших в очередь и не закончивших


class TranscodeError(Exception):
    """Перекодирование не удалось (ffmpeg упал, не уложился в таймаут, нет файла)."""


def needs_h264_transcode(info: MediaInfo | None) -> bool:
    """True, если у файла есть видеодорожка с известным кодеком, не H.264."""
    if info is None or not info.has_video or not info.video_codec:
        return False
    return info.video_codec != H264_CODEC_NAME


def is_hdr(info: MediaInfo) -> bool:
    return info.color_transfer in HDR_TRANSFERS


def effective_duration_sec(info: MediaInfo) -> float:
    """Длительность для сравнения с пределом: нулевая/неизвестная = бесконечная,
    такой ролик не должен уходить в ffmpeg до таймаута."""
    return info.duration if info.duration > 0 else math.inf


def estimate_output_mb(source_bytes: int) -> float:
    return source_bytes * TRANSCODE_OUTPUT_SIZE_RATIO / MIB


@contextlib.contextmanager
def transcode_backlog(estimate_sec: float) -> Iterator[float]:
    """Встаёт в очередь перекодирований; отдаёт оценку работы, что стоит впереди."""
    global _backlog_sec
    ahead = _backlog_sec
    _backlog_sec += estimate_sec
    try:
        yield ahead
    finally:
        _backlog_sec -= estimate_sec


def estimate_transcode_seconds(info: MediaInfo) -> float:
    """Оценка времени работы ffmpeg; для неизвестной длительности — бесконечность."""
    rate = HDR_SECONDS_PER_VIDEO_SECOND if is_hdr(info) else TRANSCODE_SECONDS_PER_VIDEO_SECOND
    return effective_duration_sec(info) * rate


def _video_filter(hdr_transfer: str | None) -> str:
    if hdr_transfer is None:
        return EVEN_SIZE_PAD_FILTER
    return f"{EVEN_SIZE_PAD_FILTER},{HDR_TO_SDR_FILTER.format(transfer=hdr_transfer)}"


def build_transcode_cmd(
    source: Path, target: Path, copy_audio: bool, hdr_transfer: str | None = None
) -> list[str]:
    """argv ffmpeg: видео в H.264, разрешение и fps исходные, звук — копия или AAC.

    `hdr_transfer` (smpte2084 / arib-std-b67) включает тонмаппинг в SDR bt709.
    """
    color_args = SDR_COLOR_TAG_ARGS if hdr_transfer else ()
    audio_args = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", AAC_FALLBACK_BITRATE]
    return [
        "ffmpeg", "-nostdin", "-y", "-v", "error",
        # Вход — недоверенный скачанный файл (см. _ffprobe_cmd в media_probe.py):
        # демуксер не должен открывать вложенные ссылки, только локальный файл.
        "-protocol_whitelist", "file",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", _video_filter(hdr_transfer),
        "-c:v", "libx264", "-preset", X264_PRESET, "-crf", X264_CRF,
        "-pix_fmt", OUTPUT_PIXEL_FORMAT,
        *color_args,
        *audio_args,
        "-movflags", "+faststart",
        str(target),
    ]


def _with_low_priority(cmd: list[str]) -> list[str]:
    nice_path = shutil.which("nice")
    return [nice_path, "-n", NICE_PRIORITY, *cmd] if nice_path else cmd


def _transcode_slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _slots_by_loop:
        _slots_by_loop[loop] = asyncio.Semaphore(MAX_CONCURRENT_TRANSCODES)
    return _slots_by_loop[loop]


async def _run_ffmpeg(cmd: list[str]) -> None:
    async with _transcode_slots():
        await _run_ffmpeg_exclusive(_with_low_priority(cmd))


async def _run_ffmpeg_exclusive(cmd: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=TRANSCODE_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        raise TranscodeError(f"ffmpeg не уложился в {TRANSCODE_TIMEOUT_SEC} с") from None
    finally:
        # Таймаут или отмена задачи оставляют процесс живым — добиваем и ждём.
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
    if process.returncode != 0:
        tail = stderr.decode(errors="replace").strip()[:STDERR_LOG_LIMIT_CHARS]
        raise TranscodeError(f"ffmpeg завершился с кодом {process.returncode}: {tail}")


def _publish_transcoded(temp_path: Path, source: Path) -> Path:
    """Ставит перекодированный файл на место источника (контейнер всегда mp4)."""
    final_path = source.with_suffix(OUTPUT_EXTENSION)
    os.replace(temp_path, final_path)
    if final_path != source:
        source.unlink(missing_ok=True)
    return final_path


async def _encode(source: Path, temp_path: Path, info: MediaInfo, hdr_transfer: str | None) -> None:
    cmd = build_transcode_cmd(
        source, temp_path, copy_audio=info.audio_codec == AAC_CODEC_NAME, hdr_transfer=hdr_transfer,
    )
    await _run_ffmpeg(cmd)


async def _encode_with_tonemap_fallback(
    source: Path, temp_path: Path, info: MediaInfo, on_tonemap_fallback: Callable[[], None] | None
) -> None:
    """HDR кодируем с тонмаппингом; если цепочка упала, один раз без него — файл
    с блёклыми цветами лучше, чем ошибка."""
    if not is_hdr(info):
        await _encode(source, temp_path, info, None)
        return
    try:
        await _encode(source, temp_path, info, info.color_transfer)
    except TranscodeError as exc:
        logger.warning("Tonemap chain failed, retrying without tonemap | error={}", exc)
        if on_tonemap_fallback is not None:
            on_tonemap_fallback()
        await _encode(source, temp_path, info, None)


async def transcode_to_h264(
    source: Path, info: MediaInfo, on_tonemap_fallback: Callable[[], None] | None = None
) -> Path:
    """Перекодирует `source` в H.264 и возвращает путь итогового mp4.

    Источник заменяется результатом. При любой ошибке временный файл
    убирается, источник остаётся нетронутым, наружу летит `TranscodeError`.
    `on_tonemap_fallback` вызывается, когда HDR-цепочка упала и идёт повтор без неё.
    """
    temp_path = source.with_name(f"{source.stem}{TRANSCODED_SUFFIX}{OUTPUT_EXTENSION}")
    logger.info(
        "Transcoding to h264 | from={} duration={:.1f}s height={} hdr={}",
        info.video_codec, info.duration, info.height, is_hdr(info),
    )
    try:
        await _encode_with_tonemap_fallback(source, temp_path, info, on_tonemap_fallback)
        return _publish_transcoded(temp_path, source)
    except OSError as exc:
        raise TranscodeError(f"не удалось запустить или сохранить перекодирование: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
