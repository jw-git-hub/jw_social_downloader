import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware

THROTTLE_NOTICE = "⏳ Слишком часто. Подожди пару секунд."


class ThrottleMiddleware(BaseMiddleware):
    """Ограничитель частоты на пользователя.

    Один экземпляр обслуживает один поток событий: для сообщений и для
    колбэков регистрируются РАЗНЫЕ экземпляры с разными лимитами и
    раздельными словарями отметок. Колбэк — это навигация по меню, и лимит
    сообщений в три секунды сделал бы её неюзабельной.
    """

    def __init__(self, rate_limit: float = 3.0, *, notify: bool = False) -> None:
        self.rate_limit = rate_limit
        self.notify = notify
        self.user_timestamps: dict[int, float] = {}
        self.notified_at: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            # Сообщение от анонимного админа группы или пост канала: троттлить
            # некого. Раньше здесь был AttributeError прямо в middleware,
            # то есть до любого хендлера.
            return await handler(event, data)

        user_id = user.id
        now = time.monotonic()

        last = self.user_timestamps.get(user_id)
        if last is not None and (now - last) < self.rate_limit:
            if self.notify:
                await self._notify_throttled(event, user_id, now)
            return None

        self.user_timestamps[user_id] = now
        self._evict_stale(now)
        return await handler(event, data)

    async def _notify_throttled(self, event: Any, user_id: int, now: float) -> None:
        """Сообщить об отбое не чаще раза за окно.

        Для колбэка это ещё и обязательный `answer()`: без него у пользователя
        висит крутилка до 30 секунд, и троттлинг кнопок сделал бы интерфейс
        хуже, а не лучше.
        """
        last_notice = self.notified_at.get(user_id)
        if last_notice is not None and (now - last_notice) < self.rate_limit:
            return
        self.notified_at[user_id] = now

        answer = getattr(event, "answer", None)
        if answer is None:
            return
        try:
            await answer(THROTTLE_NOTICE)
        except Exception:
            # Юзер мог заблокировать бота ровно сейчас. Молчим.
            pass

    def _evict_stale(self, now: float) -> None:
        """Оба словаря чистятся вместе, иначе второй растёт неограниченно."""
        for store in (self.user_timestamps, self.notified_at):
            stale = [uid for uid, ts in store.items() if (now - ts) > self.rate_limit]
            for uid in stale:
                del store[uid]
