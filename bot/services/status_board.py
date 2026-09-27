"""Правки одного статус-сообщения с троттлингом, устойчивые к сбоям Telegram.

Загрузка и отправка большого файла занимают минуты, и всё это время нет
других поводов написать пользователю — фоновый тикер (`start_ticking`) правит
статус раз в `PROGRESS_EDIT_INTERVAL_SEC`, читая свежий текст через `render`.
Любой сбой `edit_text` — сеть, флуд-лимит, удалённое сообщение — должен
проглатываться: правка статуса никогда не должна ронять саму загрузку или
отправку (см. `bot/handlers/user.py::_download_with_progress`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from loguru import logger

PROGRESS_EDIT_INTERVAL_SEC = 4.0  # Telegram: ~1 правка/с на чат; с запасом на 3 параллельные загрузки
EDIT_TIMEOUT_SEC = 10.0  # правка статуса не должна задерживать отправку
NOT_MODIFIED_MARKER = "message is not modified"


class StatusBoard:
    """Одна доска на статус-сообщение: правки, тикер, best-effort доставка."""

    def __init__(
        self,
        message,
        *,
        interval: float = PROGRESS_EDIT_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
        initial_text: str | None = None,
    ) -> None:
        self._message = message
        self._interval = interval
        self._clock = clock
        self._enabled = True
        # Сообщение уже отправлено с этим текстом (см. `message.reply(text)`
        # у вызывающего) — первый `show(initial_text)` не должен слать
        # лишнюю правку тем же текстом (M-7, ревью очереди 2026-09-27).
        self._last_text: str | None = initial_text
        self._silenced_until = 0.0
        self._tick_task: asyncio.Task | None = None

    async def show(self, text: str) -> None:
        """Best-effort правка. Ничего не бросает наружу (кроме CancelledError:
        сам он Exception не наследует и генерируемым здесь except не ловится)."""
        if not self._may_attempt(text):
            return
        try:
            await asyncio.wait_for(self._message.edit_text(text), EDIT_TIMEOUT_SEC)
        except TelegramRetryAfter as exc:
            self._silenced_until = self._clock() + exc.retry_after
            return
        except TelegramBadRequest as exc:
            self._handle_bad_request(exc, text)
            return
        except TelegramForbiddenError as exc:
            self._disable(exc)
            return
        except Exception as exc:
            logger.debug("Правка статуса не удалась | error={}", exc)
            return
        self._last_text = text

    def _may_attempt(self, text: str) -> bool:
        if not self._enabled or text == self._last_text:
            return False
        return self._clock() >= self._silenced_until

    def _handle_bad_request(self, exc: TelegramBadRequest, text: str) -> None:
        if NOT_MODIFIED_MARKER in str(exc).lower():
            self._last_text = text
            return
        self._disable(exc)

    def _disable(self, exc: Exception) -> None:
        logger.info("Статус-доска отключена | error={}", exc)
        self._enabled = False

    async def start_ticking(self, render: Callable[[], str]) -> None:
        if self._tick_task is not None:
            await self.stop_ticking()
        self._tick_task = asyncio.create_task(self._tick_loop(render))

    async def stop_ticking(self) -> None:
        task = self._tick_task
        self._tick_task = None
        if task is None:
            return
        task.cancel()
        # `await task` напрямую (старый код) неотличим от отмены САМОГО
        # вызывающего: оба CancelledError всплывают на одной и той же
        # await-точке, и `suppress(CancelledError)` глотал оба разом — если
        # вызывающего отменяли (например, `_process_download` при остановке
        # бота), эта отмена терялась молча (M-3, ревью очереди 2026-09-27).
        # `asyncio.wait` ждёт завершения задачи, но не пробрасывает её
        # исключение в текущую корутину — отмену вызывающего он не глотает.
        await asyncio.wait([task])

    @asynccontextmanager
    async def ticking(self, render: Callable[[], str]) -> AsyncIterator[None]:
        await self.start_ticking(render)
        try:
            yield
        finally:
            await self.stop_ticking()

    async def _tick_loop(self, render: Callable[[], str]) -> None:
        while True:
            await asyncio.sleep(self._interval)
            text = self._safe_render(render)
            if text is not None:
                await self.show(text)

    def _safe_render(self, render: Callable[[], str]) -> str | None:
        try:
            return render()
        except Exception as exc:
            logger.debug("Рендер статуса упал | error={}", exc)
            return None
