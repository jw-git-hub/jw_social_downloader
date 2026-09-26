from types import SimpleNamespace

from bot.config import settings
from bot.db.free_quota import free_quota_status, refund_free_download
from bot.db.models import User
from bot.handlers.user import _quota_action, _reserve_quota


def _tg_user(uid: int = 555001, username: str = "quota_tester", full_name: str = "Quota Tester"):
    return SimpleNamespace(id=uid, username=username, full_name=full_name)


async def _left(session, uid: int) -> int:
    return (await free_quota_status(session, uid)).left


# ── политика возврата брони ──


def test_quota_action_keeps_when_nothing_was_reserved():
    assert _quota_action(False, download_ok=False, media_sent_count=0) == "keep"
    assert _quota_action(False, download_ok=True, media_sent_count=0) == "keep"


def test_quota_action_refunds_when_download_failed():
    assert _quota_action(True, download_ok=False, media_sent_count=0) == "refund"


def test_quota_action_refunds_when_nothing_was_delivered():
    assert _quota_action(True, download_ok=True, media_sent_count=0) == "refund"


def test_quota_action_keeps_when_at_least_one_file_delivered():
    assert _quota_action(True, download_ok=True, media_sent_count=1) == "keep"
    assert _quota_action(True, download_ok=True, media_sent_count=5) == "keep"


# ── бронь в одной транзакции с чтением ──


async def test_reserve_quota_takes_one_slot_from_new_user(db_session):
    async with db_session.begin():
        uid, banned, has_sub, reservation_id = await _reserve_quota(db_session, _tg_user())
    assert (banned, has_sub) == (False, False)
    assert isinstance(reservation_id, int)
    assert await _left(db_session, uid) == settings.FREE_DOWNLOADS_PER_DAY - 1


async def test_fourth_download_of_the_day_is_refused(db_session):
    tg = _tg_user()
    async with db_session.begin():
        results = [(await _reserve_quota(db_session, tg))[3] for _ in range(settings.FREE_DOWNLOADS_PER_DAY + 1)]
    assert all(r is not None for r in results[:-1])
    assert results[-1] is None


async def test_reserve_quota_does_not_touch_banned_user(db_session):
    tg = _tg_user()
    async with db_session.begin():
        db_session.add(User(id=tg.id, username=tg.username, full_name=tg.full_name, is_banned=True))
        await db_session.flush()
        _, banned, _, reservation_id = await _reserve_quota(db_session, tg)
    assert banned is True and reservation_id is None
    assert await _left(db_session, tg.id) == settings.FREE_DOWNLOADS_PER_DAY


async def test_reserve_quota_skips_subscriber(db_session):
    from datetime import datetime, timedelta, timezone

    tg = _tg_user()
    until = datetime.now(timezone.utc) + timedelta(days=5)
    async with db_session.begin():
        db_session.add(User(id=tg.id, username=tg.username, full_name=tg.full_name, subscription_until=until))
        await db_session.flush()
        _, _, has_sub, reservation_id = await _reserve_quota(db_session, tg)
    assert has_sub is True and reservation_id is None
    assert await _left(db_session, tg.id) == settings.FREE_DOWNLOADS_PER_DAY


async def test_refund_returns_exactly_the_reserved_slot(db_session):
    tg = _tg_user()
    async with db_session.begin():
        _, _, _, reservation_id = await _reserve_quota(db_session, tg)
        await refund_free_download(db_session, reservation_id)
    assert await _left(db_session, tg.id) == settings.FREE_DOWNLOADS_PER_DAY
