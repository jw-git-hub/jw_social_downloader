"""Бесплатный лимит: FREE_DOWNLOADS_PER_DAY скачиваний за скользящие 24 часа."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import settings
from bot.db.free_quota import (
    FREE_WINDOW,
    free_quota_status,
    refund_free_download,
    reserve_free_download,
)
from bot.db.models import FreeDownload, User

UID = 1000000001
T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


async def _reserve_n(session, n, now=T0):
    return [await reserve_free_download(session, UID, now) for _ in range(n)]


async def _rows(session) -> int:
    return await session.scalar(
        select(func.count()).select_from(FreeDownload).where(FreeDownload.user_id == UID)
    )


async def test_three_per_day_then_refusal(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        ids = await _reserve_n(db_session, 3)
        fourth = await reserve_free_download(db_session, UID, T0)

    assert all(isinstance(i, int) for i in ids)
    assert len(set(ids)) == 3
    assert fourth is None


async def test_slot_reopens_exactly_after_24_hours(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        await _reserve_n(db_session, 3)
        just_before = await reserve_free_download(db_session, UID, T0 + FREE_WINDOW - timedelta(seconds=1))
        at_24h = await reserve_free_download(db_session, UID, T0 + FREE_WINDOW)

    assert just_before is None
    assert at_24h is not None


async def test_expired_rows_are_removed_so_table_does_not_grow(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        await _reserve_n(db_session, 3)
        await reserve_free_download(db_session, UID, T0 + timedelta(hours=25))
        assert await _rows(db_session) == 1


async def test_refund_returns_the_slot(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        ids = await _reserve_n(db_session, 3)
        await refund_free_download(db_session, ids[-1])
        assert (await free_quota_status(db_session, UID, T0)).left == 1
        assert await reserve_free_download(db_session, UID, T0) is not None


async def test_double_refund_returns_only_one_slot(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        ids = await _reserve_n(db_session, 2)
        await refund_free_download(db_session, ids[0])
        await refund_free_download(db_session, ids[0])
        assert (await free_quota_status(db_session, UID, T0)).left == settings.FREE_DOWNLOADS_PER_DAY - 1


async def test_status_reports_left_and_next_opening(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        await reserve_free_download(db_session, UID, T0)
        await reserve_free_download(db_session, UID, T0 + timedelta(hours=1))
        partial = await free_quota_status(db_session, UID, T0 + timedelta(hours=2))
        await reserve_free_download(db_session, UID, T0 + timedelta(hours=2))
        full = await free_quota_status(db_session, UID, T0 + timedelta(hours=3))

    assert partial.left == 1 and partial.next_at is None
    assert full.left == 0
    assert full.next_at == T0 + FREE_WINDOW  # освобождается самая старая бронь


async def test_status_when_limit_lowered_below_used(db_session, make_user, monkeypatch):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        for hour in range(3):
            await reserve_free_download(db_session, UID, T0 + timedelta(hours=hour))
    monkeypatch.setattr(settings, "FREE_DOWNLOADS_PER_DAY", 1)

    status = await free_quota_status(db_session, UID, T0 + timedelta(hours=3))

    # Занято 3 при лимите 1: слот откроется, когда в окне станет 0 броней,
    # то есть когда истечёт самая свежая из трёх (T0 + 2 ч).
    assert status.left == 0
    assert status.next_at == T0 + timedelta(hours=2) + FREE_WINDOW


async def test_new_user_has_full_quota(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    status = await free_quota_status(db_session, UID, T0)
    assert status.left == settings.FREE_DOWNLOADS_PER_DAY
    assert status.next_at is None


async def test_concurrent_reservations_for_last_slot_yield_one_winner(
    db_session, make_user, tmp_path, sqlite_engine_factory
):
    """Два независимых соединения к одному файлу БД. Barrier — прямо перед бронью,
    иначе первая попытка успевает закоммититься и гонки нет; перед ним — чтение
    пользователя, как в хендлере (get_or_create_user)."""
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        await _reserve_n(db_session, settings.FREE_DOWNLOADS_PER_DAY - 1)

    engine_b = sqlite_engine_factory(tmp_path / "test.db")
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)
    barrier = asyncio.Barrier(2)

    async def attempt(session):
        async with session.begin():
            await session.get(User, UID)
            await barrier.wait()
            return await reserve_free_download(session, UID, T0)

    try:
        async with maker_b() as session_b:
            results = await asyncio.gather(attempt(db_session), attempt(session_b))
    finally:
        await engine_b.dispose()

    assert sum(r is not None for r in results) == 1
    assert (await free_quota_status(db_session, UID, T0)).left == 0
