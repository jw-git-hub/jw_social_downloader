from datetime import datetime, timedelta, timezone

from bot.db.queries import get_user_by_username


async def test_lookup_ignores_case(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(username="ivan"))

    found = await get_user_by_username(db_session, "@Ivan")

    assert found is not None
    assert found.id == 1000000001


async def test_lookup_strips_at_sign_and_spaces(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(username="ivan"))

    assert await get_user_by_username(db_session, "  @IVAN  ") is not None


async def test_duplicate_nicks_do_not_raise_and_pick_the_recent_one(db_session, make_user):
    """M-22: раньше здесь был MultipleResultsFound и полная тишина в ответ."""
    old = datetime(2025, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
    async with db_session.begin():
        db_session.add(make_user(1000000001, username="foo", updated_at=old))
        db_session.add(make_user(1000000002, username="Foo", updated_at=recent))

    found = await get_user_by_username(db_session, "@foo")

    assert found is not None
    assert found.id == 1000000002


async def test_unknown_nick_returns_none(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(username="ivan"))

    assert await get_user_by_username(db_session, "@petr") is None


async def test_empty_input_returns_none(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(username="ivan"))

    assert await get_user_by_username(db_session, "@") is None
    assert await get_user_by_username(db_session, "   ") is None
