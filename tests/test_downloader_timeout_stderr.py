"""Хвост stderr на таймауте (поправка главной сессии, деградация #3).

Раньше `Download timeout` отбрасывал stderr вместе с отменённым
`communicate()` — в логах не оставалось ни одной строки о причине. После
перехода на построчное чтение stdout `communicate_streaming` копит stderr во
внешний `stderr_sink` (см. `bot/services/process_stream.py`), и его можно
прочитать, даже если саму корутину отменит `asyncio.wait_for` по таймауту —
залогировать хвост (константа на число строк, вывод пропущен через ту же
маскировку секретов, что и остальные логи загрузчика).

Важно: `process.stderr.read()` заново после отмены НЕ работает надёжно —
`asyncio.StreamReader.read()` без размера потребляет внутренний буфер
частями через локальные переменные самого `read()`, и уже прочитанные, но не
возвращённые байты пропадают вместе с кадром отменённой корутины. Поэтому
`_stderr_tail` — синхронная функция над уже готовым `bytearray`, а не новый
асинхронный `read()`.
"""

from __future__ import annotations

import sys

import pytest
from loguru import logger

from bot.services import downloader
from bot.services.downloader import TIMEOUT_STDERR_TAIL_LINES, _stderr_tail

# ── _stderr_tail: юнит на готовом буфере ──────────────────────────────────


def test_stderr_tail_keeps_only_last_n_lines():
    lines = [f"line-{i}" for i in range(1, 31)]  # 30 строк, лимит 20
    buffer = bytearray(("\n".join(lines) + "\n").encode())

    tail = _stderr_tail(buffer)

    tail_lines = tail.splitlines()
    assert len(tail_lines) == TIMEOUT_STDERR_TAIL_LINES
    assert tail_lines[0] == "line-11"
    assert tail_lines[-1] == "line-30"
    assert "line-1\n" not in tail and not tail.startswith("line-1 ")


def test_stderr_tail_handles_empty_buffer():
    assert _stderr_tail(bytearray()) == ""


def test_stderr_tail_never_raises_on_undecodable_bytes():
    tail = _stderr_tail(bytearray(b"\xff\xfe not valid utf-8 \xff"))
    assert isinstance(tail, str)


# ── Сквозной таймаут: хвост доходит до лога, замаскирован, ограничен ──────


@pytest.fixture
def captured_logs():
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def _write_slow_ytdlp_with_stderr_lines(bin_dir, line_count: int) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "yt-dlp"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        f"for i in range(1, {line_count + 1}):\n"
        "    sys.stderr.write(f'stderr-line-{i} https://cdn.example.com/x?sig=SUPERSECRETVALUE\\n')\n"
        "    sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )
    script.chmod(0o755)


async def test_timeout_logs_masked_stderr_tail_without_changing_the_error_message(
    tmp_path, monkeypatch, captured_logs
):
    bin_dir = tmp_path / "bin"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    monkeypatch.setattr(downloader.settings, "DOWNLOAD_TIMEOUT", 0.3)
    _write_slow_ytdlp_with_stderr_lines(bin_dir, line_count=30)

    result = await downloader.download_media("https://www.youtube.com/watch?v=xyz", "youtube")

    # Текст и поведение таймаута не изменились (правило брифа: тексты ошибок
    # не трогаем).
    assert result.success is False
    assert "Таймаут" in result.error_message

    warning_messages = "\n".join(captured_logs)
    assert "Download timeout" in warning_messages
    # Только хвост: старые строки не должны попасть в лог.
    assert "stderr-line-1 " not in warning_messages
    assert "stderr-line-30" in warning_messages
    # Число строк хвоста ограничено константой, а не всем stderr.
    tail_line_count = sum(1 for line in warning_messages.splitlines() if line.startswith("stderr-line-"))
    assert tail_line_count == TIMEOUT_STDERR_TAIL_LINES
    # Секрет замаскирован той же mask_secrets, что и остальные логи.
    assert "SUPERSECRETVALUE" not in warning_messages
    assert "<redacted>" in warning_messages
