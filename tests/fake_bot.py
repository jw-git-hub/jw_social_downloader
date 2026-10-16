"""Бот-двойник для тестов хендлера отправки: помнит, какие chat action ему слали."""

from __future__ import annotations


class FakeBot:
    id = 1

    def __init__(self) -> None:
        self.chat_actions: list[str] = []
        self.last_request_timeout: float | None = None

    async def send_chat_action(self, chat_id, action, message_thread_id=None, request_timeout=None):
        self.chat_actions.append(action)
        self.last_request_timeout = request_timeout
