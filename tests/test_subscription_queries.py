from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from bot.db.models import SubscriptionGrant, User
from bot.db.queries import apply_subscription_change

ADMIN = 1000000777


async def _grant_rows(session) -> int:
    return await session.scalar(select(func.count(SubscriptionGrant.id)))


async def test_grant_extends_from_now_for_a_fresh_user(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())

    async with db_session.begin():
        outcome = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    assert outcome.applied is True
    assert outcome.duplicate is False
    assert outcome.user_found is True
    delta = outcome.subscription_until - datetime.now(timezone.utc)
    assert timedelta(days=29) < delta < timedelta(days=31)


async def test_repeated_key_changes_nothing(db_session, make_user):
    """H-12: двойной клик «+30 дней» из-за лага давал 60 дней за одну оплату."""
    async with db_session.begin():
        db_session.add(make_user())

    async with db_session.begin():
        first = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )
    async with db_session.begin():
        second = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    assert second.duplicate is True
    assert second.applied is False
    assert second.subscription_until == first.subscription_until
    assert await _grant_rows(db_session) == 1


async def test_different_keys_stack_as_intended(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())

    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=7, idempotency_key="k1"
        )
    async with db_session.begin():
        second = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=7, idempotency_key="k2"
        )

    delta = second.subscription_until - datetime.now(timezone.utc)
    assert timedelta(days=13) < delta < timedelta(days=15)
    assert await _grant_rows(db_session) == 2


async def test_negative_days_shorten_the_subscription(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    async with db_session.begin():
        shortened = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=-7, idempotency_key="k2"
        )

    delta = shortened.subscription_until - datetime.now(timezone.utc)
    assert timedelta(days=22) < delta < timedelta(days=24)


async def test_none_days_clears_the_subscription(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    async with db_session.begin():
        cleared = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=None, idempotency_key="k2"
        )

    assert cleared.applied is True
    assert cleared.subscription_until is None
    stored = await db_session.scalar(select(User.subscription_until).where(User.id == 1000000001))
    assert stored is None


async def test_missing_user_is_reported_not_silently_ignored(db_session):
    """M-24: grant на отсутствующего юзера рапортовал успех."""
    async with db_session.begin():
        outcome = await apply_subscription_change(
            db_session, user_id=1000000999, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    assert outcome.user_found is False
    assert outcome.applied is False
    assert await _grant_rows(db_session) == 0


async def test_journal_records_who_did_what(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    row = await db_session.scalar(select(SubscriptionGrant))
    assert row.user_id == 1000000001
    assert row.admin_id == ADMIN
    assert row.days == 30
    assert row.idempotency_key == "k1"
    assert row.subscription_until_after is not None
