"""Платежи звёздами: запись списаний, продление подписки, возврат.

Подписка меняется только через apply_subscription_change — у каждого
изменения есть строка журнала и ключ от повтора: `stars:<charge_id>` для
оплаты, `refund:<charge_id>` для возврата.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import StarPayment
from bot.db.queries import apply_subscription_change

SUBSCRIPTION_DAYS = 30
# admin_id в журнале subscription_grant для изменений, которые сделал сам бот.
SYSTEM_ACTOR_ID = 0


@dataclass(frozen=True)
class PaymentOutcome:
    """`duplicate` — этот платёж уже был применён; `subscription_until` (UTC) — конец подписки."""

    duplicate: bool
    is_first: bool
    subscription_until: datetime | None


def _is_first(payment) -> bool:
    return bool(payment.is_first_recurring) or not payment.is_recurring


def _expiration(payment) -> datetime | None:
    if not payment.subscription_expiration_date:
        return None
    return datetime.fromtimestamp(payment.subscription_expiration_date, timezone.utc)


async def _subscription_charge_id(session: AsyncSession, user_id: int, payment) -> str:
    """Номер первого платежа подписки. У продления это последний невозвращённый
    первый платёж пользователя; если его нет — свой номер."""
    if _is_first(payment):
        return payment.telegram_payment_charge_id
    first = await session.scalar(
        select(StarPayment.telegram_payment_charge_id)
        .where(
            StarPayment.user_id == user_id,
            StarPayment.is_first.is_(True),
            StarPayment.refunded_at.is_(None),
        )
        .order_by(StarPayment.id.desc())
        .limit(1)
    )
    return first or payment.telegram_payment_charge_id


async def apply_star_payment(session: AsyncSession, *, user_id: int, payment) -> PaymentOutcome:
    """Записывает списание и продлевает подписку на SUBSCRIPTION_DAYS.

    Пользователь обязан существовать (хендлер зовёт get_or_create_user в той
    же транзакции). Звать внутри транзакции, которую коммитит вызывающий.
    Повтор того же платежа ничего не меняет и возвращает duplicate=True.
    """
    is_first = _is_first(payment)
    await session.execute(
        sqlite_insert(StarPayment.__table__)
        .values(
            user_id=user_id,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            subscription_charge_id=await _subscription_charge_id(session, user_id, payment),
            amount=payment.total_amount,
            invoice_payload=payment.invoice_payload,
            is_first=is_first,
            subscription_expiration=_expiration(payment),
        )
        .on_conflict_do_nothing(index_elements=["telegram_payment_charge_id"])
    )
    grant = await apply_subscription_change(
        session,
        user_id=user_id,
        admin_id=SYSTEM_ACTOR_ID,
        days=SUBSCRIPTION_DAYS,
        idempotency_key=f"stars:{payment.telegram_payment_charge_id}",
        reason="stars_first" if is_first else "stars_renewal",
    )
    return PaymentOutcome(
        duplicate=grant.duplicate, is_first=is_first, subscription_until=grant.subscription_until
    )


async def list_user_payments(session: AsyncSession, user_id: int, limit: int) -> list[StarPayment]:
    """Последние платежи пользователя, новые сверху."""
    result = await session.scalars(
        select(StarPayment)
        .where(StarPayment.user_id == user_id)
        .order_by(StarPayment.id.desc())
        .limit(limit)
    )
    return list(result.all())


async def get_payment(session: AsyncSession, payment_id: int) -> StarPayment | None:
    return await session.get(StarPayment, payment_id)


async def apply_refund(
    session: AsyncSession, *, payment_id: int, admin_id: int, now: datetime | None = None
) -> bool:
    """Отмечает платёж возвращённым и снимает подписку. False — платежа нет
    или он уже был возвращён (повторный вызов ничего не меняет)."""
    payment = await session.get(StarPayment, payment_id)
    if payment is None:
        return False
    marked = await session.execute(
        update(StarPayment)
        .where(StarPayment.id == payment_id, StarPayment.refunded_at.is_(None))
        .values(refunded_at=now or datetime.now(timezone.utc))
    )
    if marked.rowcount == 0:
        return False
    await apply_subscription_change(
        session,
        user_id=payment.user_id,
        admin_id=admin_id,
        days=None,
        idempotency_key=f"refund:{payment.telegram_payment_charge_id}",
        reason="stars_refund",
    )
    return True
