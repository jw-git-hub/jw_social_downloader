from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

FFPROBE_TIMEOUT = 30


@dataclass(frozen=True)
class MediaInfo:
    has_video: bool
    has_audio: bool
    duration: float
    width: int
    height: int


def _first_float(*values: object) -> float:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _rotation(stream: dict) -> int:
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(side_data["rotation"])
            except (TypeError, ValueError):
                return 0
    return 0


def parse_ffprobe_json(raw: str) -> MediaInfo | None:
    """Разбирает вывод `ffprobe -print_format json`. None — если вывод нечитаем."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = height = 0
    if video is not None:
        width = int(_first_float(video.get("width")))
        height = int(_first_float(video.get("height")))
        # Повёрнутое видео: Telegram ждёт отображаемые размеры, а не размеры кадра.
        if abs(_rotation(video)) % 180 == 90:
            width, height = height, width

    duration = _first_float(
        fmt.get("duration"),
        video.get("duration") if video else None,
        audio.get("duration") if audio else None,
    )

    return MediaInfo(
        has_video=video is not None,
        has_audio=audio is not None,
        duration=duration,
        width=width,
        height=height,
    )


def video_reject_reason(info: MediaInfo | None) -> str | None:
    """Причина, по которой файл нельзя отдавать как видео. None — можно."""
    if info is None:
        return "Не удалось прочитать метаданные файла"
    if not info.has_video:
        return "В файле нет видеодорожки"
    if not info.has_audio:
        return "В файле нет звука"
    if info.duration <= 0:
        return "У файла нулевая длительность"
    if info.width <= 0 or info.height <= 0:
        return "У файла не определяется разрешение"
    return None


async def probe_media(path: Path) -> MediaInfo | None:
    """Запускает ffprobe. Никогда не бросает — при любой беде возвращает None."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams", "-show_format",
        "-print_format", "json",
        str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=FFPROBE_TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logger.warning("ffprobe timeout | path={}", path)
            return None
    except Exception as exc:
        logger.warning("ffprobe failed | path={} error={}", path, exc)
        return None

    return parse_ffprobe_json(stdout.decode(errors="replace"))
