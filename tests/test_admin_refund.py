from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

import bot.handlers.admin_payments as AP
from bot.config import settings
from bot.db.models import StarPayment, User
from bot.keyboards.inline import get_payments_kb, get_user_card_kb
from tests.test_user_handle_url import _make_session_maker

UID = 600000001


class FakeBot:
    def __init__(self, refund_error=None, cancel_error=None):
        self.refund_error = refund_error
        self.cancel_error = cancel_error
        self.cancelled = []
        self.refunded = []
        self.sent = []

    async def edit_user_star_subscription(self, user_id, telegram_payment_charge_id, is_canceled):
        self.cancelled.append((user_id, telegram_payment_charge_id, is_canceled))
        if self.cancel_error:
            raise self.cancel_error

    async def refund_star_payment(self, user_id, telegram_payment_charge_id):
        self.refunded.append((user_id, telegram_payment_charge_id))
        if self.refund_error:
            raise self.refund_error

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)


class FakeCallback:
    def __init__(self, data, bot, uid=None):
        self.data = data
        self.bot = bot
        self.from_user = SimpleNamespace(id=settings.ADMIN_ID if uid is None else uid)
        self.message = None  # _safe_edit шлёт новым сообщением через bot.send_message
        self.toasts = []

    async def answer(self, text=None, **kwargs):
        self.toasts.append(text)


def _bad_request(text):
    return TelegramBadRequest(method=None, message=text)


async def _setup(monkeypatch, sqlite_engine_factory, tmp_path, *, refunded=False):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "refund.db")
    monkeypatch.setattr(AP, "async_session", maker)
    async with maker() as session, session.begin():
        session.add(User(id=UID, username="p", full_name="P",
                         subscription_until=datetime(2026, 10, 26, tzinfo=timezone.utc)))
        await session.flush()
        session.add(StarPayment(
            id=1, user_id=UID, telegram_payment_charge_id="ch_first", subscription_charge_id="ch_first",
            amount=250, invoice_payload="sub_30d", is_first=True,
            refunded_at=datetime.now(timezone.utc) if refunded else None,
        ))
        await session.flush()
        session.add(StarPayment(
            id=2, user_id=UID, telegram_payment_charge_id="ch_renew", subscription_charge_id="ch_first",
            amount=250, invoice_payload="sub_30d", is_first=False,
        ))
    return maker, engine


async def _state(maker, payment_id):
    async with maker() as session:
        return (await session.get(User, UID)).subscription_until, (await session.get(StarPayment, payment_id)).refunded_at


async def test_refund_cancels_renewal_by_first_charge_and_removes_subscription(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot()
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot))
        assert bot.cancelled == [(UID, "ch_first", True)]
        assert bot.refunded == [(UID, "ch_renew")]
        until, refunded_at = await _state(maker, 2)
        assert until is None and refunded_at is not None
    finally:
        await engine.dispose()


async def test_already_refunded_in_telegram_counts_as_success(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot(refund_error=_bad_request("Bad Request: CHARGE_ALREADY_REFUNDED"))
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot))
        until, refunded_at = await _state(maker, 2)
        assert until is None and refunded_at is not None
    finally:
        await engine.dispose()


async def test_telegram_refusal_leaves_database_untouched(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot(refund_error=_bad_request("Bad Request: CHARGE_NOT_FOUND"))
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot))
        until, refunded_at = await _state(maker, 2)
        assert until is not None and refunded_at is None
        assert any("CHARGE_NOT_FOUND" in text for text in bot.sent)
    finally:
        await engine.dispose()


async def test_cancel_failure_does_not_block_refund(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot(cancel_error=_bad_request("Bad Request: SUBSCRIPTION_NOT_ACTIVE"))
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot))
        assert bot.refunded == [(UID, "ch_renew")]
        assert (await _state(maker, 2))[1] is not None
    finally:
        await engine.dispose()


async def test_refunded_payment_is_not_refunded_again(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path, refunded=True)
    try:
        bot = FakeBot()
        callback = FakeCallback("admin:refund_do:1", bot)
        await AP.cb_refund_do(callback)
        assert bot.refunded == [] and bot.cancelled == []
        assert "Уже возвращён" in callback.toasts
    finally:
        await engine.dispose()


async def test_non_admin_cannot_refund(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot()
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot, uid=424242))
        assert bot.refunded == [] and bot.sent == []
    finally:
        await engine.dispose()


async def test_garbage_callback_is_answered_quietly(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        for data in ("admin:refund_do:abc", "admin:refund_ask:", "admin:payments:x", "admin:card:"):
            callback = FakeCallback(data, FakeBot())
            handler = {
                "admin:refund_do": AP.cb_refund_do, "admin:refund_ask": AP.cb_refund_ask,
                "admin:payments": AP.cb_payments, "admin:card": AP.cb_card,
            }[data.rsplit(":", 1)[0]]
            await handler(callback)
            assert callback.toasts == [None]
    finally:
        await engine.dispose()


async def test_payments_screen_lists_and_offers_refund_only_for_unrefunded(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path, refunded=True)
    try:
        bot = FakeBot()
        await AP.cb_payments(FakeCallback(f"admin:payments:{UID}", bot))
        assert "250 ⭐" in bot.sent[0] and "возвращён" in bot.sent[0]
    finally:
        await engine.dispose()


async def test_payments_screen_for_user_without_payments(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot()
        await AP.cb_payments(FakeCallback("admin:payments:999", bot))
        assert "нет" in bot.sent[0]
    finally:
        await engine.dispose()


def _callback_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_refund_buttons_only_for_unrefunded_payments_and_fit_limit():
    payments = [
        SimpleNamespace(id=10, amount=250, refunded_at=None, created_at=datetime(2026, 9, 26)),
        SimpleNamespace(id=11, amount=250, refunded_at=datetime(2026, 9, 27), created_at=datetime(2026, 9, 20)),
    ]
    data = _callback_data(get_payments_kb(9999999999999999, payments))
    assert "admin:refund_ask:10" in data and "admin:refund_ask:11" not in data
    assert "admin:card:9999999999999999" in data
    assert all(len(d.encode()) <= 64 for d in data)


def test_user_card_has_payments_button():
    assert "admin:payments:42" in _callback_data(get_user_card_kb(42))
