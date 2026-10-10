"""Перекодирование видео в H.264 для клиентов Telegram, не умеющих VP9/AV1.

Instagram отдаёт лучшее разрешение (1080p) только в VP9; iPhone-клиент
Telegram VP9 не показывает — картинка стоит на первом кадре, звук идёт.
Решение о перекодировании принимается по факту: кодек готового файла берётся
из ffprobe (`bot/services/media_probe.py`), а не угадывается по списку форматов.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from loguru import logger

from bot.services.media_probe import MediaInfo

H264_CODEC_NAME = "h264"
AAC_CODEC_NAME = "aac"
# Замер 2026-10-10 на RK3399 в prod-образе (libx264 veryfast crf 20, 1080x1920
# 30 fps, VP9-источник): 8,4 с видео кодируются за ~20 с, то есть ~2,4 с на
# секунду видео при загруженном хосте. 90 с ролика — это ~3,6 минуты работы.
# Ролики длиннее не перекодируем, а качаем в H.264 пониженного разрешения.
TRANSCODE_MAX_DURATION_SEC = 90
TRANSCODE_SECONDS_PER_VIDEO_SECOND = 2.5  # с запасом к замеру, для оценки «осталось» в статусе
TRANSCODE_TIMEOUT_SEC = 600
# crf 20 / veryfast: на VP9-источнике визуально без потерь (источник уже сжат
# сильнее), файл ~3x больше источника; crf 18 давал ещё +27 % размера за то же время.
X264_PRESET = "veryfast"
X264_CRF = "20"
OUTPUT_PIXEL_FORMAT = "yuv420p"
AAC_FALLBACK_BITRATE = "192k"
TRANSCODED_SUFFIX = "_h264"
OUTPUT_EXTENSION = ".mp4"
STDERR_LOG_LIMIT_CHARS = 300


class TranscodeError(Exception):
    """Перекодирование не удалось (ffmpeg упал, не уложился в таймаут, нет файла)."""


def needs_h264_transcode(info: MediaInfo | None) -> bool:
    """True, если у файла есть видеодорожка с известным кодеком, не H.264."""
    if info is None or not info.has_video or not info.video_codec:
        return False
    return info.video_codec != H264_CODEC_NAME


def estimate_transcode_seconds(info: MediaInfo) -> float:
    return info.duration * TRANSCODE_SECONDS_PER_VIDEO_SECOND


def build_transcode_cmd(source: Path, target: Path, copy_audio: bool) -> list[str]:
    """argv ffmpeg: видео в H.264, разрешение и fps исходные, звук — копия или AAC."""
    audio_args = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", AAC_FALLBACK_BITRATE]
    return [
        "ffmpeg", "-nostdin", "-y", "-v", "error",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", X264_PRESET, "-crf", X264_CRF,
        "-pix_fmt", OUTPUT_PIXEL_FORMAT,
        *audio_args,
        "-movflags", "+faststart",
        str(target),
    ]


async def _run_ffmpeg(cmd: list[str]) -> None:
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


async def transcode_to_h264(source: Path, info: MediaInfo) -> Path:
    """Перекодирует `source` в H.264 и возвращает путь итогового mp4.

    Источник заменяется результатом. При любой ошибке временный файл
    убирается, источник остаётся нетронутым, наружу летит `TranscodeError`.
    """
    temp_path = source.with_name(f"{source.stem}{TRANSCODED_SUFFIX}{OUTPUT_EXTENSION}")
    cmd = build_transcode_cmd(source, temp_path, copy_audio=info.audio_codec == AAC_CODEC_NAME)
    logger.info(
        "Transcoding to h264 | from={} duration={:.1f}s height={}",
        info.video_codec, info.duration, info.height,
    )
    try:
        await _run_ffmpeg(cmd)
        return _publish_transcoded(temp_path, source)
    except OSError as exc:
        raise TranscodeError(f"не удалось запустить или сохранить перекодирование: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
