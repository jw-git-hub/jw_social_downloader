"""Админка: платежи пользователя и возврат звёзд с отменой автопродления."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from loguru import logger

from bot.db.engine import async_session
from bot.db.payments import apply_refund, get_payment, list_user_payments
from bot.db.queries import get_user_by_id
from bot.handlers.admin import _card_text, _format_dt, _safe_edit, is_admin
from bot.keyboards.inline import (
    get_admin_menu_kb,
    get_payments_kb,
    get_refund_confirm_kb,
    get_user_card_kb,
)
from bot.utils.text import esc

router = Router(name="admin_payments")

PAYMENTS_IN_CARD = 10
# Повторный возврат того же платежа Telegram отклоняет этой ошибкой — деньги уже у человека.
ALREADY_REFUNDED_MARKER = "CHARGE_ALREADY_REFUNDED"


def _tail_id(data: str) -> int | None:
    """Число после последнего двоеточия; мусор → None."""
    try:
        return int(data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _payment_line(payment) -> str:
    kind = "первый" if payment.is_first else "продление"
    status = " · ↩️ возвращён" if payment.refunded_at else ""
    return f"{_format_dt(payment.created_at)} · {payment.amount} ⭐ · {kind}{status}"


def _payments_text(user_id: int, payments) -> str:
    header = f"💸 <b>Платежи</b> <code>{user_id}</code>\n\n"
    if not payments:
        return header + "Платежей звёздами нет."
    return header + "\n".join(_payment_line(p) for p in payments)


async def _show_payments(callback: CallbackQuery, user_id: int) -> None:
    async with async_session() as session, session.begin():
        payments = await list_user_payments(session, user_id, PAYMENTS_IN_CARD)
    await _safe_edit(callback, _payments_text(user_id, payments), reply_markup=get_payments_kb(user_id, payments))


async def _load_payment(callback: CallbackQuery):
    """Платёж из callback_data или None (тогда колбэк уже отвечен)."""
    payment_id = _tail_id(callback.data)
    payment = None
    if payment_id is not None:
        async with async_session() as session, session.begin():
            payment = await get_payment(session, payment_id)
    if payment is None:
        await callback.answer()
    return payment


@router.callback_query(F.data.startswith("admin:payments:"))
async def cb_payments(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    user_id = _tail_id(callback.data)
    if user_id is not None:
        await _show_payments(callback, user_id)


@router.callback_query(F.data.startswith("admin:refund_ask:"))
async def cb_refund_ask(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    payment = await _load_payment(callback)
    if payment is None:
        return
    if payment.refunded_at is not None:
        await callback.answer("Уже возвращён")
        return
    await callback.answer()
    text = (
        f"Вернуть <b>{payment.amount} ⭐</b> за платёж от {_format_dt(payment.created_at)}?\n\n"
        "Подписка будет снята, автопродление отключено."
    )
    await _safe_edit(callback, text, reply_markup=get_refund_confirm_kb(payment.id, payment.user_id))


@router.callback_query(F.data.startswith("admin:refund_do:"))
async def cb_refund_do(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    payment = await _load_payment(callback)
    if payment is None:
        return
    if payment.refunded_at is not None:
        await callback.answer("Уже возвращён")
        return
    error = await _refund_in_telegram(callback.bot, payment)
    if error is not None:
        await callback.answer()
        await _safe_edit(
            callback,
            f"❌ Telegram отказал в возврате:\n<code>{esc(error)}</code>",
            reply_markup=get_refund_confirm_kb(payment.id, payment.user_id),
        )
        return
    async with async_session() as session, session.begin():
        await apply_refund(session, payment_id=payment.id, admin_id=callback.from_user.id)
    logger.info("Возврат звёзд | payment={} user={} amount={}", payment.id, payment.user_id, payment.amount)
    await callback.answer("✅ Возвращено")
    await _show_payments(callback, payment.user_id)


async def _refund_in_telegram(bot, payment) -> str | None:
    """None — звёзды у человека (вернули сейчас или раньше); иначе текст отказа.

    Сначала отменяем автопродление (по номеру ПЕРВОГО платежа — иначе Telegram
    его не найдёт). Его отказ не останавливает возврат: подписка могла уже
    закончиться или быть отменена самим пользователем.
    """
    try:
        await bot.edit_user_star_subscription(
            user_id=payment.user_id,
            telegram_payment_charge_id=payment.subscription_charge_id,
            is_canceled=True,
        )
    except TelegramAPIError as exc:
        logger.warning("Автопродление не отменено | payment={} error={}", payment.id, exc)
    try:
        await bot.refund_star_payment(
            user_id=payment.user_id, telegram_payment_charge_id=payment.telegram_payment_charge_id
        )
    except TelegramAPIError as exc:
        if ALREADY_REFUNDED_MARKER in str(exc).upper():
            return None
        logger.warning("Возврат отклонён Telegram | payment={} error={}", payment.id, exc)
        return str(exc)
    return None


@router.callback_query(F.data.startswith("admin:card:"))
async def cb_card(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    user_id = _tail_id(callback.data)
    if user_id is None:
        return
    async with async_session() as session, session.begin():
        user = await get_user_by_id(session, user_id)
        text = await _card_text(session, user) if user else None
    if text is None:
        await _safe_edit(callback, "❌ Пользователь не найден.", reply_markup=get_admin_menu_kb())
        return
    await _safe_edit(callback, text, reply_markup=get_user_card_kb(user_id))
