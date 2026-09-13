import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

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

    # Ревью фикс-раунда 1: мутация «журналировать только настоящие продления»
    # (session.add(...) под `if days is not None:`, то есть отмены вообще не
    # пишутся в журнал) раньше оставляла все 7 тестов зелёными — эта
    # проверка обязана красить именно такую мутацию.
    cancel_row = await db_session.scalar(
        select(SubscriptionGrant).where(SubscriptionGrant.idempotency_key == "k2")
    )
    assert cancel_row is not None
    assert cancel_row.days is None
    assert cancel_row.subscription_until_after is None


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


async def test_journal_records_before_state_and_reason(db_session, make_user):
    """Ревью фикс-раунда 1: журнал отвечал на «кому/когда/кем/сколько», но
    не на «с какого состояния» и «за что» — строка без `reason`/`before`
    самодостаточной не была, историю приходилось собирать цепочкой."""
    async with db_session.begin():
        db_session.add(make_user())

    async with db_session.begin():
        await apply_subscription_change(
            db_session,
            user_id=1000000001,
            admin_id=ADMIN,
            days=30,
            idempotency_key="k1",
            reason="payment #42",
        )
    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=7, idempotency_key="k2"
        )

    # Оба чтения — после обеих записей и вне отдельного begin(): бесхозный
    # `scalar()` между двумя `async with db_session.begin()` блоками
    # оставляет на сессии автостартовавшую транзакцию, и следующий
    # `.begin()` падает `InvalidRequestError: A transaction is already
    # begun` — поймано эмпирически при первом прогоне этого теста.
    first_row = await db_session.scalar(
        select(SubscriptionGrant).where(SubscriptionGrant.idempotency_key == "k1")
    )
    second_row = await db_session.scalar(
        select(SubscriptionGrant).where(SubscriptionGrant.idempotency_key == "k2")
    )

    assert first_row.subscription_until_before is None  # подписки не было вовсе
    assert first_row.reason == "payment #42"
    # "before" второй строки — это ровно "after" первой: строка самодостаточна,
    # цепочку можно восстановить, но конкретно эта проверка ничего не цепляет.
    assert second_row.subscription_until_before == first_row.subscription_until_after
    assert second_row.reason is None  # reason необязателен, дефолт — None


async def test_concurrent_same_key_calls_yield_one_winner(
    db_session, make_user, tmp_path, sqlite_engine_factory
):
    """Ревью фикс-раунда 1 (H-12): у aiogram 3.31 `Dispatcher` нет
    `tasks_concurrency_limit`, `ThrottleMiddleware` не висит на
    `dp.callback_query` — конкурентный (не последовательный!) двойной клик
    по одной кнопке админской карточки является основным путём, не краем.

    Форма — как у `test_concurrent_reservations_yield_exactly_one_winner`
    в `test_quota_queries.py` (C-1): два независимых соединения к одному
    файлу БД, `asyncio.Barrier` НЕПОСРЕДСТВЕННО перед вызовом (иначе первая
    попытка успевает закоммититься до старта второй и гонки не будет), и
    предварительный `session.get()` в каждой ветке — форма будущего вызова
    из хендлера пакета E.

    Тест обязан не просто вернуть правильные значения, а НЕ УРОНИТЬ
    исключение ни в одной из веток — именно это было дефектом
    SELECT-then-INSERT версии (см. красный прогон в отчёте: одна из веток
    получала необработанный `IntegrityError` вместо `duplicate=True`).
    `asyncio.gather` без `return_exceptions=True` сам провалит тест, если
    хоть одна из веток бросит исключение.
    """
    async with db_session.begin():
        db_session.add(make_user())

    engine_b = sqlite_engine_factory(tmp_path / "test.db")
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)
    barrier = asyncio.Barrier(2)

    async def attempt_a():
        async with db_session.begin():
            await db_session.get(User, 1000000001)
            await barrier.wait()
            return await apply_subscription_change(
                db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="race"
            )

    async def attempt_b():
        async with maker_b() as session:
            async with session.begin():
                await session.get(User, 1000000001)
                await barrier.wait()
                return await apply_subscription_change(
                    session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="race"
                )

    try:
        results = await asyncio.gather(attempt_a(), attempt_b())
    finally:
        await engine_b.dispose()

    applied = [r for r in results if r.applied]
    duplicates = [r for r in results if r.duplicate]
    assert len(applied) == 1  # ровно один победитель
    assert len(duplicates) == 1  # и ровно один штатный (не исключением!) проигравший
    assert await _grant_rows(db_session) == 1  # задвоения журнала нет
