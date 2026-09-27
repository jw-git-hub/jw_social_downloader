"""Аналог `process.communicate()`, читающий stdout построчно по мере появления.

Нужен, чтобы `bot/services/downloader.py` мог отдавать прогресс yt-dlp
(`bot/services/ytdlp_progress.py`) вызывающему коду ДО завершения процесса —
обычный `communicate()` возвращает stdout только целиком, когда процесс уже
закончился.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from loguru import logger

# Строка JWPLAN (список форматов, 52–69 штук на живом ролике) занимает до
# ~10 КБ — дефолтные 64 КиБ лимита StreamReader впритык на длинных списках.
STREAM_LINE_LIMIT_BYTES = 1024 * 1024
# Размер порции при чтении stderr. Читаем чанками, а не одним await read() —
# так вызывающий может получить `stderr_sink` (см. communicate_streaming) с
# уже дочитанными чанками ДАЖЕ если сама функция отменена таймаутом снаружи:
# у asyncio.StreamReader.read(-1) без аргумента буфер потребляется частями во
# ВНУТРЕННИХ локальных переменных read() и пропадает вместе с кадром
# отменённой корутины — то, что уже долетело до `stderr_sink`, не пропадает,
# потому что это отдельный объект, а не локальная переменная.
STDERR_CHUNK_BYTES = 64 * 1024


async def _read_stdout_lines(
    stdout: asyncio.StreamReader, on_line: Callable[[str], bool]
) -> bytes:
    """Строки, потреблённые `on_line` (вернул True), не попадают в результат.

    `readline()` бросает `ValueError`, если строка длиннее лимита буфера —
    StreamReader к этому моменту уже отбросил переполнившие данные сам
    (см. `asyncio.StreamReader.readline`), поэтому просто читаем дальше.
    """
    kept = bytearray()
    while True:
        try:
            raw = await stdout.readline()
        except ValueError:
            continue
        if not raw:
            break
        try:
            consumed = on_line(raw.decode(errors="replace"))
        except Exception as exc:
            logger.debug("on_stdout_line callback failed: {}", exc)
            consumed = False
        if not consumed:
            kept.extend(raw)
    return bytes(kept)


async def _read_stderr_chunks(stderr: asyncio.StreamReader, sink: bytearray | None) -> bytes:
    """Читает stderr целиком чанками (не одним `read()`), опционально
    зеркаля каждый дочитанный чанк в `sink` — см. `STDERR_CHUNK_BYTES`.
    """
    chunks = bytearray()
    while True:
        chunk = await stderr.read(STDERR_CHUNK_BYTES)
        if not chunk:
            break
        chunks.extend(chunk)
        if sink is not None:
            sink.extend(chunk)
    return bytes(chunks)


async def communicate_streaming(
    process: asyncio.subprocess.Process,
    on_stdout_line: Callable[[str], bool],
    *,
    stderr_sink: bytearray | None = None,
) -> tuple[bytes, bytes]:
    """Как `process.communicate()`, но зовёт `on_stdout_line` на каждой строке
    stdout по мере её появления, ещё пока процесс жив.

    stderr читается целиком ПАРАЛЛЕЛЬНО (`asyncio.gather`) — иначе
    переполненный пайп stderr повесит процесс, ждущий, пока кто-то освободит
    его буфер, пока мы построчно вычитываем stdout.

    `stderr_sink` — необязательный внешний буфер: вызывающий может передать
    свой `bytearray` и читать из него уже дочитанные чанки stderr ДАЖЕ если
    эту функцию отменили (например, `asyncio.wait_for` по таймауту) — см.
    докстринг `STDERR_CHUNK_BYTES`. Без него поведение не отличается от
    обычного `communicate()`.

    Таймаутом функция не занимается: вызывающий оборачивает её в
    `asyncio.wait_for`, как раньше оборачивал `communicate()`.
    """
    stdout_bytes, stderr_bytes = await asyncio.gather(
        _read_stdout_lines(process.stdout, on_stdout_line),
        _read_stderr_chunks(process.stderr, stderr_sink),
    )
    await process.wait()
    return stdout_bytes, stderr_bytes
