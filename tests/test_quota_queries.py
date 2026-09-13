import asyncio

from sqlalchemy import select

from bot.db.models import User
from bot.db.queries import refund_free_download, reserve_free_download


async def _left(session, user_id: int) -> int:
    return await session.scalar(select(User.free_downloads_left).where(User.id == user_id))


async def test_reservation_succeeds_and_decrements(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=3))

    async with db_session.begin():
        assert await reserve_free_download(db_session, 1000000001) is True

    assert await _left(db_session, 1000000001) == 2


async def test_reservation_fails_on_empty_quota_and_changes_nothing(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=0))

    async with db_session.begin():
        assert await reserve_free_download(db_session, 1000000001) is False

    assert await _left(db_session, 1000000001) == 0


async def test_last_unit_can_be_taken_only_once(db_session, make_user):
    """C-1: с одним оставшимся скачиванием пользователь получал 3–7."""
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=1))

    async with db_session.begin():
        first = await reserve_free_download(db_session, 1000000001)
        second = await reserve_free_download(db_session, 1000000001)

    assert (first, second) == (True, False)
    assert await _left(db_session, 1000000001) == 0


async def test_reservation_on_missing_user_returns_false(db_session):
    async with db_session.begin():
        assert await reserve_free_download(db_session, 1000000999) is False


async def test_refund_returns_exactly_one_unit(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=1))

    async with db_session.begin():
        await reserve_free_download(db_session, 1000000001)
        await refund_free_download(db_session, 1000000001)

    assert await _left(db_session, 1000000001) == 1


async def test_refund_on_missing_user_does_not_raise(db_session):
    async with db_session.begin():
        await refund_free_download(db_session, 1000000999)
