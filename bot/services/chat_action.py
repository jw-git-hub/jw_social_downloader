"""«Отправляет видео…» в шапке чата, пока файл уходит пользователю.

Свой цикл вместо `aiogram.utils.chat_action.ChatActionSender`: тот при выходе
ждёт возврата уже начатого `send_chat_action` (а у сессии бота таймаут
запроса — сотни секунд), а первая же ошибка убивает его воркер с «Task
exception was never retrieved» мимо loguru. Здесь, как в `StatusBoard`,
остановка — мгновенная отмена задачи, а сбой одного запроса цикл переживает.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from aiogram import Bot
from aiogram.enums import ChatAction

CHAT_ACTION_INTERVAL_SEC = 5.0  # Telegram гасит действие через ~5 с без обновления
CHAT_ACTION_TIMEOUT_SEC = 10  # один запрос действия не должен висеть дольше


class ChatActionLoop:
    """Фоновый цикл `send_chat_action` на время долгой отправки."""

    def __init__(self, bot: Bot, chat_id: int, action: ChatAction) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._action = action
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        """Отменяет цикл и сразу возвращается.

        Ожидания нет намеренно: отменённая задача больше ничего не пошлёт, а
        `await` здесь могла бы прервать повторная отмена вызывающего и
        сорвать уборку в его `finally`.
        """
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()

    async def _run(self) -> None:
        while True:
            with suppress(Exception):
                await self._bot.send_chat_action(
                    chat_id=self._chat_id,
                    action=self._action,
                    request_timeout=CHAT_ACTION_TIMEOUT_SEC,
                )
            await asyncio.sleep(CHAT_ACTION_INTERVAL_SEC)
