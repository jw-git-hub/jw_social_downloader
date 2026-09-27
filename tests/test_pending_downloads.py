"""Тесты на bot.db.pending_downloads.

`db_session` — фикстура из `tests/conftest.py`: чистая SQLite-схема (в т.ч.
`PRAGMA foreign_keys=ON`), поэтому пользователей и брони сеем сами.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from bot.db.models import FreeDownload, User
from bot.db.pending_downloads import add_pending, release_interrupted, remove_pending


async def _seed_user(session, user_id: int) -> None:
    session.add(User(id=user_id, username="tester", full_name="Test User"))
    await session.flush()


async def test_add_pending_writes_a_row_with_created_at(db_session):
    await _seed_user(db_session, 1)
    created_at = datetime(2026, 9, 27, tzinfo=timezone.utc)

    pending_id = await add_pending(
        db_session,
        user_id=1,
        chat_id=100,
        url="https://youtu.be/a",
        reservation_id=None,
        now=created_at,
    )
    await db_session.commit()

    assert pending_id is not None
    rows = await release_interrupted(db_session)
    await db_session.commit()

    assert len(rows) == 1
    assert rows[0].url == "https://youtu.be/a"
    assert rows[0].chat_id == 100
    assert rows[0].created_at == created_at


async def test_remove_pending_twice_is_safe(db_session):
    await _seed_user(db_session, 2)
    pending_id = await add_pending(
        db_session, user_id=2, chat_id=200, url="https://youtu.be/b", reservation_id=None
    )
    await db_session.commit()

    await remove_pending(db_session, pending_id)
    await remove_pending(db_session, pending_id)  # повтор — no-op
    await db_session.commit()

    rows = await release_interrupted(db_session)
    assert rows == []


async def test_release_interrupted_returns_rows_in_order_and_empties_the_log(db_session):
    await _seed_user(db_session, 3)
    await add_pending(
        db_session, user_id=3, chat_id=300, url="https://youtu.be/first", reservation_id=None
    )
    await add_pending(
        db_session, user_id=3, chat_id=300, url="https://youtu.be/second", reservation_id=None
    )
    await db_session.commit()

    rows = await release_interrupted(db_session)
    await db_session.commit()

    assert [row.url for row in rows] == ["https://youtu.be/first", "https://youtu.be/second"]
    assert await release_interrupted(db_session) == []


async def test_release_interrupted_refunds_its_own_reservation(db_session):
    await _seed_user(db_session, 4)
    db_session.add(FreeDownload(user_id=4, reserved_at=datetime.now(timezone.utc)))
    await db_session.flush()
    reservation_id = (
        await db_session.scalar(select(FreeDownload.id).where(FreeDownload.user_id == 4))
    )

    await add_pending(
        db_session,
        user_id=4,
        chat_id=400,
        url="https://youtu.be/c",
        reservation_id=reservation_id,
    )
    await db_session.commit()

    rows = await release_interrupted(db_session)
    await db_session.commit()

    assert rows[0].refunded is True
    remaining = await db_session.scalar(select(FreeDownload).where(FreeDownload.id == reservation_id))
    assert remaining is None


async def test_release_interrupted_does_not_refund_a_reused_id_of_another_user(db_session):
    """Без AUTOINCREMENT SQLite может выдать id удалённой строки `free_download`
    заново другому пользователю — сверка `user_id` должна это ловить."""
    await _seed_user(db_session, 5)
    await _seed_user(db_session, 6)

    db_session.add(FreeDownload(user_id=5, reserved_at=datetime.now(timezone.utc)))
    await db_session.flush()
    other_users_reservation_id = (
        await db_session.scalar(select(FreeDownload.id).where(FreeDownload.user_id == 5))
    )

    # Строка журнала принадлежит пользователю 6, но её reservation_id
    # совпадает с бронью пользователя 5 — переиспользованный rowid.
    await add_pending(
        db_session,
        user_id=6,
        chat_id=600,
        url="https://youtu.be/d",
        reservation_id=other_users_reservation_id,
    )
    await db_session.commit()

    rows = await release_interrupted(db_session)
    await db_session.commit()

    assert rows[0].refunded is True  # для пользователя 6 это выглядит как бронь
    still_there = await db_session.scalar(
        select(FreeDownload).where(FreeDownload.id == other_users_reservation_id)
    )
    assert still_there is not None  # но чужая бронь пользователя 5 не тронута
