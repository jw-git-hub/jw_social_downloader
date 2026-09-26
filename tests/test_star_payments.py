from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

from bot.db.models import StarPayment, SubscriptionGrant, User
from bot.db.payments import SUBSCRIPTION_DAYS, apply_refund, apply_star_payment, list_user_payments
from bot.db.queries import _as_utc

UID = 1000000001


def _payment(charge="ch_first", *, first=True, amount=250):
    return SimpleNamespace(
        telegram_payment_charge_id=charge,
        total_amount=amount,
        invoice_payload="sub_30d",
        is_recurring=True,
        is_first_recurring=True if first else None,
        subscription_expiration_date=int(datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()),
    )


async def _pay(session, payment, user_id=UID):
    async with session.begin():
        return await apply_star_payment(session, user_id=user_id, payment=payment)


async def _until(session, user_id=UID):
    # Обёрнуто в session.begin(): голый session.get() между двумя явными
    # begin()-блоками сам открывает транзакцию (autobegin) и не закрывает
    # её — следующий `async with session.begin()` падает с
    # InvalidRequestError («A transaction is already begun on this
    # Session»). Тот же эффект и первопричина, что в
    # test_subscription_queries.py::test_journal_records_before_state_and_reason.
    session.expire_all()
    async with session.begin():
        user = await session.get(User, user_id)
        return _as_utc(user.subscription_until)


async def _count(session, model) -> int:
    async with session.begin():
        return await session.scalar(select(func.count()).select_from(model))


async def _last_payment_id(session, user_id=UID) -> int:
    """Id последнего платежа — тем же приёмом, что _until: без своего
    begin() голый список платежей блокировал бы следующий session.begin()."""
    async with session.begin():
        return (await list_user_payments(session, user_id, 10))[0].id


async def test_first_payment_gives_30_days(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    before = datetime.now(timezone.utc)

    outcome = await _pay(db_session, _payment())

    assert outcome.duplicate is False and outcome.is_first is True
    until = await _until(db_session)
    assert before + timedelta(days=SUBSCRIPTION_DAYS) <= until <= datetime.now(timezone.utc) + timedelta(days=SUBSCRIPTION_DAYS)
    row = (await list_user_payments(db_session, UID, 10))[0]
    assert row.subscription_charge_id == "ch_first" and row.amount == 250 and row.is_first


async def test_same_payment_twice_extends_once(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    until_once = await _until(db_session)

    again = await _pay(db_session, _payment())

    assert again.duplicate is True
    assert await _until(db_session) == until_once
    assert await _count(db_session, StarPayment) == 1
    assert await _count(db_session, SubscriptionGrant) == 1


async def test_renewal_shifts_end_by_30_days(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    first_end = await _until(db_session)

    renewal = await _pay(db_session, _payment("ch_renewal", first=False))

    assert renewal.is_first is False
    assert await _until(db_session) == first_end + timedelta(days=SUBSCRIPTION_DAYS)
    rows = await list_user_payments(db_session, UID, 10)
    assert rows[0].telegram_payment_charge_id == "ch_renewal"
    assert rows[0].subscription_charge_id == "ch_first"  # отмена автопродления — по первому платежу


async def test_payment_stacks_on_manual_subscription(db_session, make_user):
    manual_end = datetime.now(timezone.utc) + timedelta(days=10)
    async with db_session.begin():
        db_session.add(make_user(subscription_until=manual_end))
    await _pay(db_session, _payment())
    assert await _until(db_session) == manual_end + timedelta(days=SUBSCRIPTION_DAYS)


async def test_refund_removes_subscription_and_marks_payment(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    payment_id = await _last_payment_id(db_session)

    async with db_session.begin():
        applied = await apply_refund(db_session, payment_id=payment_id, admin_id=1)

    assert applied is True
    assert await _until(db_session) is None
    db_session.expire_all()
    assert (await db_session.get(StarPayment, payment_id)).refunded_at is not None


async def test_second_refund_is_a_noop(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    payment_id = await _last_payment_id(db_session)
    async with db_session.begin():
        await apply_refund(db_session, payment_id=payment_id, admin_id=1)
    grants = await _count(db_session, SubscriptionGrant)

    async with db_session.begin():
        again = await apply_refund(db_session, payment_id=payment_id, admin_id=1)

    assert again is False
    assert await _count(db_session, SubscriptionGrant) == grants


async def test_refund_of_unknown_payment_is_false(db_session):
    async with db_session.begin():
        assert await apply_refund(db_session, payment_id=999, admin_id=1) is False


async def test_renewal_after_refunded_first_falls_back_to_own_charge(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    first_id = await _last_payment_id(db_session)
    async with db_session.begin():
        await apply_refund(db_session, payment_id=first_id, admin_id=1)

    await _pay(db_session, _payment("ch_late_renewal", first=False))

    row = (await list_user_payments(db_session, UID, 10))[0]
    assert row.subscription_charge_id == "ch_late_renewal"


async def test_payment_creates_missing_user(db_session):
    """apply_star_payment требует существующего пользователя — хендлер зовёт
    get_or_create_user в той же транзакции. Проверяем связку."""
    from bot.db.queries import get_or_create_user

    tg = SimpleNamespace(id=UID, username=None, full_name="New Payer")
    async with db_session.begin():
        user = await get_or_create_user(db_session, tg)
        outcome = await apply_star_payment(db_session, user_id=user.id, payment=_payment())
    assert outcome.duplicate is False
    assert await _until(db_session) is not None
