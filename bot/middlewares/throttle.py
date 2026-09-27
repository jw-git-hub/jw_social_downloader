import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware

THROTTLE_NOTICE = "⏳ Слишком часто. Подожди пару секунд."


class ThrottleMiddleware(BaseMiddleware):
    """Ограничитель частоты на пользователя — ведро с запасом (GCRA).

    Один экземпляр обслуживает один поток событий: для сообщений и для
    колбэков регистрируются РАЗНЫЕ экземпляры с разными лимитами и
    раздельными словарями отметок. Колбэк — это навигация по меню, и лимит
    сообщений в три секунды сделал бы её неюзабельной.

    `burst` — сколько событий подряд проходят без ожидания `rate_limit`
    между ними (запас копится, только если события не расходуют его). При
    `burst=1` (умолчание, колбэки) поведение то же, что и раньше: строго
    одно событие за `rate_limit`. Сообщениям (Задача 1 очереди, Д8 плана
    `.superpowers/sdd/2026-09-27-queue/plan.md`) нужен запас на пачку
    ссылок — см. `MESSAGE_BURST` в `bot/__main__.py`.

    Алгоритм — generic cell rate algorithm: `full_burst_at[uid]` хранит
    момент времени, когда запас пользователя снова станет полным. Событие
    пропускается, если до этого момента осталось не больше
    `(burst - 1) * rate_limit` — то есть в запасе есть ещё хотя бы одно
    место, — и тогда этот момент сдвигается на `rate_limit` вперёд. Запас
    не может накопиться больше `burst`: после простоя `ready` пересчитывается
    от текущего времени, а не от прошлого значения.
    """

    def __init__(self, rate_limit: float = 3.0, *, burst: int = 1, notify: bool = False) -> None:
        self.rate_limit = rate_limit
        self.burst = burst
        self.notify = notify
        self.full_burst_at: dict[int, float] = {}
        self.notified_at: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        if getattr(event, "successful_payment", None) is not None:
            # Звёзды уже списаны: отброшенное известие = оплаченная, но не
            # продлённая подписка. Платёж идёт к хендлеру всегда.
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if user is None:
            # Сообщение от анонимного админа группы или пост канала: троттлить
            # некого. Раньше здесь был AttributeError прямо в middleware,
            # то есть до любого хендлера.
            return await handler(event, data)

        user_id = user.id
        now = time.monotonic()

        ready = max(self.full_burst_at.get(user_id, now), now)
        burst_window = (self.burst - 1) * self.rate_limit
        if ready - now > burst_window:
            if self.notify:
                await self._notify_throttled(event, user_id, now)
            return None

        self.full_burst_at[user_id] = ready + self.rate_limit
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
        stale_full = [uid for uid, ready in self.full_burst_at.items() if ready <= now]
        for uid in stale_full:
            del self.full_burst_at[uid]

        stale_notified = [uid for uid, ts in self.notified_at.items() if (now - ts) > self.rate_limit]
        for uid in stale_notified:
            del self.notified_at[uid]
