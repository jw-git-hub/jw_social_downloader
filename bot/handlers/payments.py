"""Оплата подписки звёздами Telegram: ссылка-счёт, проверка перед оплатой, зачисление."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery
from loguru import logger

from bot.config import settings
from bot.db.engine import async_session
from bot.db.payments import PaymentOutcome, apply_star_payment
from bot.db.queries import get_or_create_user
from bot.utils.text import esc

router = Router(name="payments")

STARS_CURRENCY = "XTR"
SUBSCRIPTION_PAYLOAD = "sub_30d"
# 30 дней — единственный период подписки, который сейчас принимает Telegram.
SUBSCRIPTION_PERIOD_SECONDS = 2592000
INVOICE_TITLE = "Подписка на 30 дней"
INVOICE_DESCRIPTION = (
    "Безлимитные скачивания из Instagram, TikTok, Facebook, Pinterest и YouTube. "
    "Продлевается автоматически каждые 30 дней."
)
CANCEL_HINT = "отменить автопродление можно в настройках Telegram → «Мои звёзды»"
STALE_INVOICE_TEXT = "Счёт устарел — откройте «Подписка» в боте и оплатите заново."
PAYMENT_PENDING_TEXT = (
    "Оплата получена. Подписку включит администратор в ближайшее время; "
    "вопросы — /paysupport."
)
USER_DATE_FORMAT = "%d.%m.%Y"
ADMIN_DATE_FORMAT = "%d.%m.%Y %H:%M UTC"

# Ссылка-счёт общая для всех: плательщика Telegram присылает в successful_payment.
# Создаём один раз на процесс — лишний запрос к Telegram на каждое открытие экрана не нужен.
_invoice_link: str | None = None


async def get_invoice_link(bot) -> str:
    global _invoice_link
    if _invoice_link is None:
        _invoice_link = await bot.create_invoice_link(
            title=INVOICE_TITLE,
            description=INVOICE_DESCRIPTION,
            payload=SUBSCRIPTION_PAYLOAD,
            currency=STARS_CURRENCY,
            prices=[LabeledPrice(label=INVOICE_TITLE, amount=settings.SUBSCRIPTION_PRICE_STARS)],
            provider_token="",
            subscription_period=SUBSCRIPTION_PERIOD_SECONDS,
        )
    return _invoice_link


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Отвечаем сразу и без базы: у Telegram на это 10 секунд.

    Цену и наличие подписки НЕ сверяем. Документация не говорит, идут ли
    продления через эту проверку; если идут — сверка с текущей ценой или
    отказ «подписка уже есть» сломали бы продления всем подписчикам.
    """
    if query.currency == STARS_CURRENCY and query.invoice_payload == SUBSCRIPTION_PAYLOAD:
        await query.answer(ok=True)
        return
    await query.answer(ok=False, error_message=STALE_INVOICE_TEXT)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    try:
        async with async_session() as session, session.begin():
            user = await get_or_create_user(session, message.from_user)
            outcome = await apply_star_payment(session, user_id=user.id, payment=payment)
    except Exception:
        logger.exception(
            "Платёж не записан | user={} charge={}",
            message.from_user.id, payment.telegram_payment_charge_id,
        )
        await notify_admin(message.bot, _payment_lost_text(message, payment))
        await _answer_quietly(message, PAYMENT_PENDING_TEXT)
        return
    if outcome.duplicate:
        logger.info("Повторная доставка платежа | charge={}", payment.telegram_payment_charge_id)
        return
    await _answer_quietly(message, _payment_done_text(outcome))
    await notify_admin(message.bot, _payment_admin_text(message, payment, outcome))


def _payment_done_text(outcome: PaymentOutcome) -> str:
    until = outcome.subscription_until.strftime(USER_DATE_FORMAT)
    if outcome.is_first:
        return (
            f"<b>Подписка оформлена</b>\nБезлимит до <b>{until}</b>. "
            f"Она продлится автоматически; {CANCEL_HINT}."
        )
    return f"Подписка продлена до <b>{until}</b>."


def _payment_admin_text(message: Message, payment, outcome: PaymentOutcome) -> str:
    user = message.from_user
    handle = f"@{esc(user.username)}" if user.username else "без ника"
    kind = "первый платёж" if outcome.is_first else "продление"
    until = outcome.subscription_until.strftime(ADMIN_DATE_FORMAT)
    return (
        f"💰 <b>{payment.total_amount} ⭐</b> — {esc(user.full_name)} ({handle}, "
        f"<code>{user.id}</code>)\n{kind}, подписка до {until}"
    )


def _payment_lost_text(message: Message, payment) -> str:
    return (
        "⚠️ <b>Платёж не записан в базу</b>\n"
        f"Пользователь <code>{message.from_user.id}</code>, {payment.total_amount} ⭐\n"
        f"Чек: <code>{esc(payment.telegram_payment_charge_id)}</code>\n"
        "Выдай 30 дней вручную."
    )


async def notify_admin(bot, text: str) -> None:
    """Сообщение админу; его недоступность не должна ломать зачисление."""
    try:
        await bot.send_message(settings.ADMIN_ID, text)
    except Exception as exc:
        logger.warning("Не удалось уведомить админа | error={}", exc)


async def _answer_quietly(message: Message, text: str) -> None:
    try:
        await message.answer(text)
    except Exception as exc:
        logger.info("Не удалось ответить плательщику | user={} error={}", message.from_user.id, exc)
