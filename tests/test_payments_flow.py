from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bot.handlers.payments as P
import bot.handlers.user as U
from bot.config import settings
from bot.db.models import User
from tests.test_user_handle_url import _make_session_maker

UID = 700000001


class _Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _query(currency="XTR", payload="sub_30d"):
    return SimpleNamespace(currency=currency, invoice_payload=payload, answer=_Recorder())


def _sp(charge="ch_1", first=True):
    return SimpleNamespace(
        telegram_payment_charge_id=charge,
        total_amount=250,
        invoice_payload="sub_30d",
        is_recurring=True,
        is_first_recurring=True if first else None,
        subscription_expiration_date=None,
    )


def _pay_message(payment, *, username="payer", full_name="Pay Er"):
    bot = SimpleNamespace(send_message=_Recorder())
    return SimpleNamespace(
        from_user=SimpleNamespace(id=UID, username=username, full_name=full_name),
        successful_payment=payment,
        bot=bot,
        answer=_Recorder(),
    )


# ── pre_checkout ──


async def test_pre_checkout_approves_stars_subscription_without_db(monkeypatch):
    def _no_db():
        raise AssertionError("pre_checkout must not touch the database")

    monkeypatch.setattr(P, "async_session", _no_db)
    query = _query()
    await P.on_pre_checkout(query)
    assert query.answer.calls == [((), {"ok": True})]


@pytest.mark.parametrize("currency,payload", [("USD", "sub_30d"), ("XTR", "other")])
async def test_pre_checkout_rejects_foreign_invoice(currency, payload):
    query = _query(currency, payload)
    await P.on_pre_checkout(query)
    (_, kwargs), = query.answer.calls
    assert kwargs["ok"] is False and "Подписка" in kwargs["error_message"]


# ── successful_payment ──


async def test_payment_activates_subscription_and_notifies(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "pay.db")
    monkeypatch.setattr(P, "async_session", maker)
    try:
        message = _pay_message(_sp())
        await P.on_successful_payment(message)

        assert "Подписка оформлена" in message.answer.calls[0][0][0]
        (admin_args, _), = message.bot.send_message.calls
        assert admin_args[0] == settings.ADMIN_ID and "250 ⭐" in admin_args[1]
        async with maker() as session:
            assert (await session.get(User, UID)).subscription_until is not None
    finally:
        await engine.dispose()


async def test_redelivered_payment_is_silent(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "dup.db")
    monkeypatch.setattr(P, "async_session", maker)
    try:
        await P.on_successful_payment(_pay_message(_sp()))
        again = _pay_message(_sp())
        await P.on_successful_payment(again)
        assert again.answer.calls == [] and again.bot.send_message.calls == []
    finally:
        await engine.dispose()


async def test_renewal_message_says_extended(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "renew.db")
    monkeypatch.setattr(P, "async_session", maker)
    try:
        await P.on_successful_payment(_pay_message(_sp()))
        renewal = _pay_message(_sp("ch_2", first=False))
        await P.on_successful_payment(renewal)
        assert "продлена" in renewal.answer.calls[0][0][0]
        assert "продление" in renewal.bot.send_message.calls[0][0][1]
    finally:
        await engine.dispose()


async def test_db_failure_alerts_admin_and_reassures_user(monkeypatch):
    def _broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(P, "async_session", _broken)
    message = _pay_message(_sp("ch_lost"))
    await P.on_successful_payment(message)

    assert message.answer.calls[0][0][0] == P.PAYMENT_PENDING_TEXT
    admin_text = message.bot.send_message.calls[0][0][1]
    assert "не записан" in admin_text and "ch_lost" in admin_text


async def test_admin_notice_escapes_name_and_handles_missing_username(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "esc.db")
    monkeypatch.setattr(P, "async_session", maker)
    try:
        message = _pay_message(_sp(), username=None, full_name="Ann <3")
        await P.on_successful_payment(message)
        admin_text = message.bot.send_message.calls[0][0][1]
        assert "Ann &lt;3" in admin_text and "без ника" in admin_text
    finally:
        await engine.dispose()


async def test_admin_blocked_bot_does_not_break_payment(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "blk.db")
    monkeypatch.setattr(P, "async_session", maker)

    async def _refuse(*a, **k):
        raise RuntimeError("bot was blocked by the user")

    try:
        message = _pay_message(_sp())
        message.bot.send_message = _refuse
        await P.on_successful_payment(message)  # не должно бросить
        assert "Подписка оформлена" in message.answer.calls[0][0][0]
    finally:
        await engine.dispose()


# ── ссылка-счёт ──


async def test_invoice_link_is_created_once_with_stars_subscription(monkeypatch):
    monkeypatch.setattr(P, "_invoice_link", None)
    created = _Recorder()

    async def _create(**kwargs):
        await created(**kwargs)
        return "https://t.me/$invoice"

    bot = SimpleNamespace(create_invoice_link=_create)
    assert await P.get_invoice_link(bot) == "https://t.me/$invoice"
    assert await P.get_invoice_link(bot) == "https://t.me/$invoice"

    (_, kwargs), = created.calls
    assert kwargs["currency"] == "XTR" and kwargs["provider_token"] == ""
    assert kwargs["subscription_period"] == 2592000
    assert kwargs["payload"] == "sub_30d"
    assert kwargs["prices"][0].amount == settings.SUBSCRIPTION_PRICE_STARS


# ── экран подписки ──


class _Callback:
    def __init__(self, uid=UID):
        self.from_user = SimpleNamespace(id=uid, username="u", full_name="U")
        self.bot = SimpleNamespace()
        self.answer = _Recorder()
        self.message = None


async def _subscribe_screen(monkeypatch, sqlite_engine_factory, tmp_path, *, until, link_error=None):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "sub.db")
    monkeypatch.setattr(U, "async_session", maker)
    async with maker() as session, session.begin():
        session.add(User(id=UID, username="u", full_name="U", subscription_until=until))
    shown = []

    async def _edit(callback, text, reply_markup=None):
        shown.append((text, reply_markup))

    async def _link(bot):
        if link_error:
            raise link_error
        return "https://t.me/$invoice"

    monkeypatch.setattr(U, "_safe_edit", _edit)
    monkeypatch.setattr(U, "get_invoice_link", _link)
    await U.cb_subscribe(_Callback())
    await engine.dispose()
    return shown[0]


def _urls(markup):
    return [b.url for row in markup.inline_keyboard for b in row if b.url]


async def test_subscribe_screen_offers_stars_purchase(monkeypatch, sqlite_engine_factory, tmp_path):
    text, markup = await _subscribe_screen(monkeypatch, sqlite_engine_factory, tmp_path, until=None)
    assert f"{settings.SUBSCRIPTION_PRICE_STARS} ⭐" in text and "/terms" in text
    assert _urls(markup) == ["https://t.me/$invoice"]


async def test_subscribe_screen_hides_purchase_while_active(monkeypatch, sqlite_engine_factory, tmp_path):
    until = datetime.now(timezone.utc) + timedelta(days=5)
    text, markup = await _subscribe_screen(monkeypatch, sqlite_engine_factory, tmp_path, until=until)
    assert "активна" in text and until.strftime("%d.%m.%Y") in text
    assert _urls(markup) == []


async def test_subscribe_screen_offers_purchase_after_expiry(monkeypatch, sqlite_engine_factory, tmp_path):
    until = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, markup = await _subscribe_screen(monkeypatch, sqlite_engine_factory, tmp_path, until=until)
    assert _urls(markup) == ["https://t.me/$invoice"]


async def test_subscribe_screen_survives_invoice_failure(monkeypatch, sqlite_engine_factory, tmp_path):
    text, markup = await _subscribe_screen(
        monkeypatch, sqlite_engine_factory, tmp_path, until=None, link_error=RuntimeError("net")
    )
    assert "временно недоступна" in text
    assert _urls(markup) == []
