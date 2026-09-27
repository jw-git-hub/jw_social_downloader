"""`communicate_streaming` на настоящих подпроцессах: колбэк должен видеть
строки stdout по мере появления (пока процесс ещё жив), а не только после
его завершения, как у `process.communicate()`.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from bot.services.process_stream import communicate_streaming

TEST_TIMEOUT_SEC = 10  # страховка от зависания: тест не должен висеть вечно


async def _spawn(script: str, *, limit: int | None = None) -> asyncio.subprocess.Process:
    kwargs = {} if limit is None else {"limit": limit}
    return await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )


async def test_callback_sees_first_line_while_process_is_still_alive():
    script = (
        "import sys, time\n"
        "print('line1', flush=True)\n"
        "time.sleep(1)\n"
        "print('line2', flush=True)\n"
    )
    process = await _spawn(script)
    seen_returncode_on_first_call = []

    def on_line(line: str) -> bool:
        if not seen_returncode_on_first_call:
            seen_returncode_on_first_call.append(process.returncode)
        return False

    await asyncio.wait_for(communicate_streaming(process, on_line), TEST_TIMEOUT_SEC)

    assert seen_returncode_on_first_call == [None]


async def test_consumed_lines_are_dropped_others_kept_stderr_kept_whole():
    script = (
        "import sys\n"
        "print('keep-1')\n"
        "print('DROP-me')\n"
        "print('keep-2')\n"
        "sys.stderr.write('err-line\\n')\n"
    )
    process = await _spawn(script)

    def on_line(line: str) -> bool:
        return "DROP" in line

    stdout, stderr = await asyncio.wait_for(communicate_streaming(process, on_line), TEST_TIMEOUT_SEC)

    text = stdout.decode()
    assert "DROP-me" not in text
    assert "keep-1" in text
    assert "keep-2" in text
    assert stderr.decode() == "err-line\n"


async def test_large_stderr_and_stdout_do_not_deadlock():
    # Переполненный пайп stderr вешает процесс, если его никто не читает,
    # пока stdout вычитывается построчно — ровно та гонка, которую должен
    # закрывать параллельный asyncio.gather внутри communicate_streaming.
    script = (
        "import sys\n"
        "sys.stderr.write('e' * 500_000)\n"
        "for i in range(1000):\n"
        "    print('stdout line', i)\n"
    )
    process = await _spawn(script)

    stdout, stderr = await asyncio.wait_for(
        communicate_streaming(process, lambda line: False), TEST_TIMEOUT_SEC
    )

    assert len(stderr) == 500_000
    assert b"stdout line 999" in stdout


async def test_callback_exception_does_not_crash_and_line_is_kept():
    script = "print('boom-line')\nprint('after')\n"
    process = await _spawn(script)

    def on_line(line: str) -> bool:
        if "boom" in line:
            raise RuntimeError("callback exploded")
        return False

    stdout, _stderr = await asyncio.wait_for(communicate_streaming(process, on_line), TEST_TIMEOUT_SEC)

    assert b"boom-line" in stdout
    assert b"after" in stdout


async def test_line_longer_than_limit_is_skipped_without_raising():
    script = (
        "print('x' * 5000)\n"
        "print('ok')\n"
    )
    process = await _spawn(script, limit=1024)
    received: list[str] = []

    def on_line(line: str) -> bool:
        received.append(line)
        return False

    stdout, _stderr = await asyncio.wait_for(communicate_streaming(process, on_line), TEST_TIMEOUT_SEC)

    assert any("ok" in line for line in received)
    assert b"ok" in stdout


async def test_process_wait_is_called_returncode_is_set():
    process = await _spawn("print('done')\n")
    await asyncio.wait_for(communicate_streaming(process, lambda line: False), TEST_TIMEOUT_SEC)
    assert process.returncode == 0


# ── stderr_sink: буфер, переживающий отмену коротины на таймауте ─────────


async def test_stderr_sink_mirrors_the_returned_stderr_on_normal_completion():
    script = "import sys\nsys.stderr.write('err-a\\nerr-b\\n')\n"
    process = await _spawn(script)
    sink = bytearray()

    _stdout, stderr = await asyncio.wait_for(
        communicate_streaming(process, lambda line: False, stderr_sink=sink), TEST_TIMEOUT_SEC
    )

    assert bytes(sink) == stderr == b"err-a\nerr-b\n"


async def test_stderr_sink_keeps_already_read_chunks_after_the_caller_cancels():
    """Тот же баг, что чинит `stderr_sink`: обычный `process.stderr.read()`
    без размера копит данные в ЛОКАЛЬНЫХ переменных самого `read()` — при
    отмене ждущей корутины (`wait_for` по таймауту) уже вычитанные из
    внутреннего буфера StreamReader байты пропадают безвозвратно. Чанковое
    чтение в `stderr_sink` (внешний объект) переживает такую отмену.
    """
    script = (
        "import sys, time\n"
        "sys.stderr.write('err-line\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )
    process = await _spawn(script)
    sink = bytearray()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            communicate_streaming(process, lambda line: False, stderr_sink=sink), timeout=0.3
        )

    assert bytes(sink) == b"err-line\n"

    process.kill()
    await process.wait()
