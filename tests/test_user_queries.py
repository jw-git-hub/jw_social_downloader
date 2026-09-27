"""get_or_create_user: чтение/создание пользователя и его устойчивость к
гонке параллельных вставок одного и того же нового id (Aiogram
`handle_as_tasks=True` + очередь ссылок `_enqueue_links`)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import User
from bot.db.queries import get_or_create_user

UID = 2000000001


def _tg_user(uid: int = UID, username: str = "tester", full_name: str = "Test User"):
    return SimpleNamespace(id=uid, username=username, full_name=full_name)


async def test_creates_new_user_with_expected_fields(db_session):
    async with db_session.begin():
        user = await get_or_create_user(db_session, _tg_user())
    assert (user.id, user.username, user.full_name) == (UID, "tester", "Test User")
    assert user.is_banned is False
    assert user.total_downloads == 0
    assert user.subscription_until is None


async def test_existing_user_username_and_full_name_are_refreshed(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(user_id=UID, username="old", full_name="Old Name"))

    db_session.expire_all()
    async with db_session.begin():
        user = await get_or_create_user(db_session, _tg_user(username="new", full_name="New Name"))

    assert (user.username, user.full_name) == ("new", "New Name")


async def test_existing_user_untouched_when_nothing_changed(db_session, make_user):
    """F10: без реального изменения строка не должна грязниться лишним
    UPDATE — проверяем это по неизменному `updated_at`, а не по счётчику
    запросов (тут нет спая на курсор, как в `test_schema_migration.py`)."""
    async with db_session.begin():
        db_session.add(make_user(user_id=UID))

    db_session.expire_all()
    async with db_session.begin():
        before = (await db_session.get(User, UID)).updated_at

    db_session.expire_all()
    async with db_session.begin():
        await get_or_create_user(db_session, _tg_user())

    db_session.expire_all()
    async with db_session.begin():
        after = (await db_session.get(User, UID)).updated_at

    assert after == before


async def test_five_concurrent_calls_for_a_new_user_create_exactly_one_row(
    db_session, tmp_path, sqlite_engine_factory
):
    """5 сообщений одного НОВОГО пользователя, обработанных конкурентно (как
    в `_enqueue_links` при `handle_as_tasks=True`), не должны падать
    `IntegrityError` — до фикса SELECT-then-INSERT ронял все ветки, кроме
    одной, победившей гонку (см. red-прогон в отчёте).

    Форма — как у `test_concurrent_same_key_calls_yield_one_winner` в
    `test_subscription_queries.py`, но на 5 независимых соединений вместо
    двух: барьер сразу после предварительного чтения (иначе первая попытка
    успевает закоммититься до старта остальных и гонки не будет),
    `asyncio.gather` без `return_exceptions=True` сам провалит тест при
    необработанном исключении в любой из веток.
    """
    tg = _tg_user()
    extra_engines = [sqlite_engine_factory(tmp_path / "test.db") for _ in range(4)]
    extra_sessions = [
        async_sessionmaker(engine, expire_on_commit=False)() for engine in extra_engines
    ]
    barrier = asyncio.Barrier(5)

    async def attempt(session):
        async with session.begin():
            await session.get(User, tg.id)
            await barrier.wait()
            return await get_or_create_user(session, tg)

    try:
        results = await asyncio.gather(attempt(db_session), *(attempt(s) for s in extra_sessions))
    finally:
        for session in extra_sessions:
            await session.close()
        for engine in extra_engines:
            await engine.dispose()

    assert [user.id for user in results] == [UID] * 5

    db_session.expire_all()
    async with db_session.begin():
        count = await db_session.scalar(
            select(func.count()).select_from(User).where(User.id == UID)
        )
    assert count == 1
