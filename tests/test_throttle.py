import pytest

from bot.middlewares.throttle import ThrottleMiddleware


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeEvent:
    """Общий стаб под Message и CallbackQuery: обоим нужен from_user и answer."""

    def __init__(self, user_id=1000000001):
        self.from_user = FakeUser(user_id) if user_id is not None else None
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


async def _passthrough(event, data):
    return "handled"


async def test_first_event_passes_through():
    mw = ThrottleMiddleware(rate_limit=10.0)
    assert await mw(_passthrough, FakeEvent(), {}) == "handled"


async def test_second_event_within_window_is_dropped():
    mw = ThrottleMiddleware(rate_limit=10.0)
    await mw(_passthrough, FakeEvent(), {})
    assert await mw(_passthrough, FakeEvent(), {}) is None


async def test_different_users_do_not_throttle_each_other():
    mw = ThrottleMiddleware(rate_limit=10.0)
    await mw(_passthrough, FakeEvent(user_id=1000000001), {})
    assert await mw(_passthrough, FakeEvent(user_id=1000000002), {}) == "handled"


async def test_event_without_from_user_is_not_throttled_and_does_not_crash():
    """M-25: сообщение от анонимного админа группы приходит без from_user."""
    mw = ThrottleMiddleware(rate_limit=10.0)
    assert await mw(_passthrough, FakeEvent(user_id=None), {}) == "handled"


async def test_throttled_event_is_answered_when_notify_is_on():
    mw = ThrottleMiddleware(rate_limit=10.0, notify=True)
    await mw(_passthrough, FakeEvent(), {})
    second = FakeEvent()
    await mw(_passthrough, second, {})
    assert len(second.answers) == 1
    assert second.answers[0]


async def test_notice_is_sent_at_most_once_per_window():
    """Ответ на флуд не должен сам становиться флудом."""
    mw = ThrottleMiddleware(rate_limit=10.0, notify=True)
    await mw(_passthrough, FakeEvent(), {})
    events = [FakeEvent() for _ in range(5)]
    for event in events:
        await mw(_passthrough, event, {})
    assert sum(len(e.answers) for e in events) == 1


async def test_notice_failure_does_not_break_the_middleware():
    class DeadEvent(FakeEvent):
        async def answer(self, text=None, **kwargs):
            raise RuntimeError("юзер заблокировал бота")

    mw = ThrottleMiddleware(rate_limit=10.0, notify=True)
    await mw(_passthrough, FakeEvent(), {})
    assert await mw(_passthrough, DeadEvent(), {}) is None


async def test_stale_entries_are_evicted():
    mw = ThrottleMiddleware(rate_limit=0.0, notify=True)
    for user_id in range(1000000001, 1000000021):
        await mw(_passthrough, FakeEvent(user_id=user_id), {})
    # rate_limit=0 означает, что все прошлые отметки протухли сразу.
    assert len(mw.user_timestamps) <= 1
    assert len(mw.notified_at) == 0
