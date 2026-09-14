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
    """Разбирает вывод `ffprobe -print_format json`.

    None — если вывод нечитаем как JSON или имеет неожиданную структуру
    (не тот тип полей, не список и т.п.). Никогда не бросает исключений.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    try:
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
    except Exception as exc:
        # Форма JSON неожиданная (не тот тип поля, не dict/list там, где ждали
        # dict/list, "inf"/"nan" в размерах и т.п.) — не наша забота чинить её,
        # наша забота не упасть.
        logger.warning("ffprobe json has unexpected structure: {}", exc)
        return None


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


# Коды причин — чтобы вызывающий мог различать их программно, а не по тексту.
REJECT_NO_METADATA = "no_metadata"
REJECT_NO_VIDEO = "no_video"
REJECT_NO_AUDIO = "no_audio"
REJECT_ZERO_DURATION = "zero_duration"
REJECT_NO_DIMENSIONS = "no_dimensions"


def video_reject_code(info: MediaInfo | None) -> str | None:
    """Машиночитаемая причина отказа. None — файл годен к отправке как видео.

    Отсутствие звука выделено отдельным кодом: это единственная причина,
    которую вызывающий обрабатывает не отказом, а повторной загрузкой.
    """
    if info is None:
        return REJECT_NO_METADATA
    if not info.has_video:
        return REJECT_NO_VIDEO
    if not info.has_audio:
        return REJECT_NO_AUDIO
    if info.duration <= 0:
        return REJECT_ZERO_DURATION
    if info.width <= 0 or info.height <= 0:
        return REJECT_NO_DIMENSIONS
    return None


def _ffprobe_cmd(path: Path) -> list[str]:
    """argv для ffprobe.

    `-protocol_whitelist file` обязателен: на вход идёт недоверенный
    скачанный файл, а демуксеры ffmpeg умеют открывать вложенные ссылки
    (например, DASH-манифест внутри файла). В Debian trixie около 25 CVE
    ffmpeg висят в статусе «vulnerable (no-dsa/postponed)» — трекер осознанно
    отказался их патчить, в их числе OOB-чтение в DASH-демуксере с апстрим-
    фиксом, не портированным в дистрибутив. Версией это не лечится, поэтому
    не даём демуксеру ходить никуда, кроме обычного файла.

    `-probesize`/`-analyzeduration` сознательно оставлены дефолтными: их
    занижение даёт ложное «нет звука» на файлах, где аудиодорожка начинается
    не с нулевой отметки, а это отказ, видимый пользователю, — теоретическая
    уязвимость не стоит того, чтобы менять её на реальные ложные отказы.
    Объём работы ffprobe и так ограничен таймаутом FFPROBE_TIMEOUT с
    последующим kill()/wait() ниже.
    """
    return [
        "ffprobe", "-v", "error",
        "-protocol_whitelist", "file",
        "-show_streams", "-show_format",
        "-print_format", "json",
        str(path),
    ]


async def probe_media(path: Path) -> MediaInfo | None:
    """Запускает ffprobe. Никогда не бросает — при любой беде возвращает None.

    Исключение — отмена задачи (CancelledError): дочерний процесс в этом
    случае убивается и дожидается, а отмена продолжает распространяться,
    как и полагается.
    """
    cmd = _ffprobe_cmd(path)
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        logger.warning("ffprobe failed to start | path={} error={}", path, exc)
        return None

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=FFPROBE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("ffprobe timeout | path={}", path)
        return None
    except Exception as exc:
        logger.warning("ffprobe failed | path={} error={}", path, exc)
        return None
    finally:
        # Таймаут или отмена задачи могли оставить процесс живым — не плодим
        # зомби и не оставляем ffprobe висеть на большом файле.
        if process.returncode is None:
            process.kill()
            await process.wait()

    if process.returncode != 0:
        logger.debug(
            "ffprobe exited non-zero | path={} code={} stderr={}",
            path, process.returncode, stderr.decode(errors="replace")[:300],
        )
        return None

    try:
        return parse_ffprobe_json(stdout.decode(errors="replace"))
    except Exception as exc:
        logger.warning("ffprobe output could not be parsed | path={} error={}", path, exc)
        return None
