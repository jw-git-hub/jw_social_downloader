import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.rate_limit: float = 3.0
        self.user_timestamps: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id
        now = time.monotonic()

        last = self.user_timestamps.get(user_id)
        if last is not None and (now - last) < self.rate_limit:
            return

        self.user_timestamps[user_id] = now

        # Evict stale entries to prevent unbounded memory growth
        stale = [
            uid for uid, ts in self.user_timestamps.items()
            if (now - ts) > self.rate_limit
        ]
        for uid in stale:
            del self.user_timestamps[uid]

        return await handler(event, data)
