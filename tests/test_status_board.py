"""StatusBoard: троттлинг правок статус-сообщения и best-effort доставка.

Двойник сообщения программируется очередью исключений на `edit_text` и
необязательным «зависанием» (await asyncio.Event().wait(), никогда не
завершается сам) — ровно то, что нужно для проверки EDIT_TIMEOUT_SEC.
"""

from __future__ import annotations

import asyncio
import contextlib

import bot.services.status_board as status_board_module
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from bot.services.status_board import StatusBoard

_METHOD = SendMessage(chat_id=1, text="x")


class _FakeMessage:
    """`edit_text` регистрирует каждую попытку, потом либо виснет, либо
    бросает следующее исключение из очереди, либо просто удаётся."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._raise_queue: list[Exception] = []
        self._hang_once = False
        self._slow_cancel_event: asyncio.Event | None = None

    def queue_raise(self, exc: Exception) -> None:
        self._raise_queue.append(exc)

    def hang_once(self) -> None:
        self._hang_once = True

    def slow_cancel(self, event: asyncio.Event) -> None:
        """Симулирует медленную отмену: `edit_text` не умирает сразу по
        `CancelledError`, а сперва ждёт внешний `event` (как реальная
        отмена сетевого запроса могла бы ждать закрытия соединения)."""
        self._slow_cancel_event = event

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.attempts.append(text)
        if self._hang_once:
            self._hang_once = False
            await asyncio.Event().wait()
        if self._slow_cancel_event is not None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self._slow_cancel_event.wait()
                raise
        if self._raise_queue:
            raise self._raise_queue.pop(0)


def _clock(start: float = 0.0):
    state = {"now": start}

    def now() -> float:
        return state["now"]

    return state, now


async def test_repeated_same_text_edits_once():
    msg = _FakeMessage()
    board = StatusBoard(msg)

    await board.show("A")
    await board.show("A")

    assert msg.attempts == ["A"]


async def test_not_modified_counts_as_success():
    msg = _FakeMessage()
    msg.queue_raise(TelegramBadRequest(method=_METHOD, message="message is not modified"))
    board = StatusBoard(msg)

    await board.show("A")  # BadRequest "not modified" -> считается успехом
    await board.show("A")  # тот же текст -> новой попытки быть не должно
    await board.show("B")

    assert msg.attempts == ["A", "B"]


async def test_retry_after_silences_updates_without_sleeping(monkeypatch):
    msg = _FakeMessage()
    state, now = _clock()
    board = StatusBoard(msg, clock=now)
    msg.queue_raise(TelegramRetryAfter(method=_METHOD, message="Too Many Requests", retry_after=30))

    await asyncio.wait_for(board.show("A"), 1)  # первая попытка ловит RetryAfter
    assert msg.attempts == ["A"]

    state["now"] = 10.0
    await asyncio.wait_for(board.show("B"), 1)  # ещё внутри окна retry_after -> не зовёт
    assert msg.attempts == ["A"]

    state["now"] = 31.0
    await asyncio.wait_for(board.show("B"), 1)  # окно истекло -> зовёт
    assert msg.attempts == ["A", "B"]


@pytest.mark.parametrize(
    "exc",
    [
        TelegramBadRequest(method=_METHOD, message="message to edit not found"),
        TelegramForbiddenError(method=_METHOD, message="bot was blocked"),
    ],
)
async def test_fatal_errors_silence_the_board_forever(exc):
    msg = _FakeMessage()
    msg.queue_raise(exc)
    board = StatusBoard(msg)

    await board.show("A")
    await board.show("B")
    await board.show("C")

    assert msg.attempts == ["A"]


async def test_unexpected_exception_is_swallowed_and_next_show_retries():
    msg = _FakeMessage()
    msg.queue_raise(RuntimeError("boom"))
    board = StatusBoard(msg)

    await board.show("A")  # RuntimeError проглочен, last_text не обновлён
    await board.show("A")  # тот же текст -> доска пробует снова

    assert msg.attempts == ["A", "A"]


async def test_hung_edit_returns_after_timeout(monkeypatch):
    monkeypatch.setattr(status_board_module, "EDIT_TIMEOUT_SEC", 0.05)
    msg = _FakeMessage()
    msg.hang_once()
    board = StatusBoard(msg)

    await asyncio.wait_for(board.show("A"), 1)  # не должно зависнуть

    assert msg.attempts == ["A"]


async def test_ticker_edits_repeatedly_and_stops_on_stop_ticking():
    msg = _FakeMessage()
    board = StatusBoard(msg, interval=0.01)
    counter = {"n": 0}

    def render() -> str:
        counter["n"] += 1
        return f"tick {counter['n']}"

    await board.start_ticking(render)
    await asyncio.sleep(0.1)
    await board.stop_ticking()

    assert len(msg.attempts) >= 2

    attempts_after_stop = len(msg.attempts)
    await asyncio.sleep(0.05)
    assert len(msg.attempts) == attempts_after_stop


async def test_ticker_survives_a_raising_render():
    msg = _FakeMessage()
    board = StatusBoard(msg, interval=0.01)
    calls = {"n": 0}

    def render() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("render boom")
        return "ok"

    await board.start_ticking(render)
    await asyncio.sleep(0.1)
    await board.stop_ticking()

    assert "ok" in msg.attempts


async def test_stop_ticking_twice_is_safe():
    msg = _FakeMessage()
    board = StatusBoard(msg)

    await board.stop_ticking()
    await board.stop_ticking()


async def test_ticking_context_manager_starts_and_stops():
    msg = _FakeMessage()
    board = StatusBoard(msg, interval=0.01)

    async with board.ticking(lambda: "inside"):
        await asyncio.sleep(0.05)

    assert msg.attempts
    attempts_after_exit = len(msg.attempts)
    await asyncio.sleep(0.05)
    assert len(msg.attempts) == attempts_after_exit


# ── M-7: initial_text не должен провоцировать лишнюю первую правку ────────


async def test_initial_text_suppresses_the_first_identical_show():
    msg = _FakeMessage()
    board = StatusBoard(msg, initial_text="A")

    await board.show("A")
    assert msg.attempts == []

    await board.show("B")
    assert msg.attempts == ["B"]


async def test_without_initial_text_the_first_show_still_edits():
    msg = _FakeMessage()
    board = StatusBoard(msg)

    await board.show("A")
    assert msg.attempts == ["A"]


# ── M-3: stop_ticking не должен глотать отмену ВЫЗЫВАЮЩЕГО ────────────────


async def test_stop_ticking_lets_the_callers_own_cancellation_through():
    """На старом коде (`with suppress(CancelledError): await task`) этот тест
    красный: оба CancelledError — тикера и вызывающего — всплывают на одной
    и той же await-точке, и `suppress` глотает их неразличимо. `outer`
    тогда тихо завершается вместо того, чтобы остаться отменённым.
    """
    msg = _FakeMessage()
    release = asyncio.Event()
    msg.slow_cancel(release)
    board = StatusBoard(msg, interval=0.01)

    await board.start_ticking(lambda: "tick")
    tick_task = board._tick_task
    for _ in range(200):
        if msg.attempts:
            break
        await asyncio.sleep(0.005)
    assert msg.attempts  # правка стартовала и зависла внутри edit_text

    outer = asyncio.create_task(board.stop_ticking())
    await asyncio.sleep(0.01)  # дать outer сделать task.cancel() и дойти до asyncio.wait

    outer.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await outer
    assert outer.cancelled()

    with contextlib.suppress(asyncio.CancelledError):
        await tick_task
