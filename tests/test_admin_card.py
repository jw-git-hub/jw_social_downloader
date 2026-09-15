from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, InaccessibleMessage

from bot.handlers.admin import _format_dt, _safe_edit, _user_card_text


def _fake_user(**overrides):
    """Минимальный дублёр строки users: только поля, которые читает карточка."""
    data = dict(
        id=1000000001,
        username="ivan",
        full_name="Ivan Petrov",
        free_downloads_left=2,
        subscription_until=None,
        total_downloads=5,
        is_banned=False,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


# ── экранирование ──


def test_card_escapes_angle_brackets_in_full_name():
    text = _user_card_text(_fake_user(full_name="Ann <3"))
    assert "Ann &lt;3" in text
    assert "Ann <3" not in text


def test_card_escapes_ampersand_in_full_name():
    # Латентная денежная ветка того же класса: реквизиты вида «NGUYEN VAN A & CO».
    text = _user_card_text(_fake_user(full_name="NGUYEN VAN A & CO"))
    assert "A &amp; CO" in text


def test_card_escapes_username():
    text = _user_card_text(_fake_user(username="a<b>c"))
    assert "a&lt;b&gt;c" in text
    assert "<b>c" not in text


def test_card_keeps_its_own_markup():
    text = _user_card_text(_fake_user())
    assert text.startswith("👤 <b>Карточка пользователя</b>")
    assert "<code>1000000001</code>" in text


def test_card_without_username_keeps_previous_wording():
    assert "📛 Username: @нет" in _user_card_text(_fake_user(username=None))


# ── таймзона ──


def test_card_without_subscription_shows_no_marker():
    assert "👑 Подписка до: ❌ Нет" in _user_card_text(_fake_user())


def test_naive_datetime_is_printed_as_utc():
    # В SQLite tzinfo не хранится, колонка отдаёт naive-UTC.
    assert _format_dt(datetime(2026, 9, 30, 20, 0, 0)) == "30.09.2026 20:00 UTC"


def test_aware_datetime_is_converted_to_utc():
    aware = datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone(timedelta(hours=7)))
    assert _format_dt(aware) == "30.09.2026 20:00 UTC"


def test_none_datetime_renders_as_absent():
    assert _format_dt(None) == "❌ Нет"


# ── _safe_edit ──


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


class _FakeMessage:
    def __init__(self, error=None):
        self.error = error
        self.edited = []

    async def edit_text(self, text, reply_markup=None):
        if self.error is not None:
            raise self.error
        self.edited.append((text, reply_markup))


class _FakeCallback:
    def __init__(self, message, bot=None):
        self.message = message
        self.bot = bot if bot is not None else _FakeBot()
        self.from_user = SimpleNamespace(id=1000000001)


def _bad_request(text):
    # method=None: TelegramAPIError только сохраняет аргумент, валидации нет.
    return TelegramBadRequest(method=None, message=text)


async def test_safe_edit_edits_when_telegram_is_happy():
    cb = _FakeCallback(_FakeMessage())
    await _safe_edit(cb, "текст")
    assert cb.message.edited == [("текст", None)]
    assert cb.bot.sent == []


async def test_safe_edit_treats_not_modified_as_success():
    cb = _FakeCallback(_FakeMessage(_bad_request("Bad Request: message is not modified")))
    await _safe_edit(cb, "тот же текст")
    # Дубль сообщения не отправлен: пользователь и так видит нужный текст.
    assert cb.bot.sent == []


async def test_safe_edit_falls_back_to_a_new_message_on_markup_error():
    cb = _FakeCallback(_FakeMessage(_bad_request("Bad Request: can't parse entities")))
    await _safe_edit(cb, "текст")
    assert [t for _, t, _ in cb.bot.sent] == ["текст"]


async def test_safe_edit_survives_non_telegram_exception():
    cb = _FakeCallback(_FakeMessage(RuntimeError("boom")))
    await _safe_edit(cb, "текст")
    assert len(cb.bot.sent) == 1


async def test_safe_edit_sends_new_message_when_message_is_none():
    cb = _FakeCallback(None)
    await _safe_edit(cb, "текст")
    assert len(cb.bot.sent) == 1


async def test_safe_edit_handles_message_older_than_48_hours():
    # Telegram отдаёт InaccessibleMessage — у него нет метода edit_text вовсе.
    # aiogram 3.31 типизирует date как Literal[0] — иначе не проходит валидацию.
    inaccessible = InaccessibleMessage(
        chat=Chat(id=1000000001, type="private"),
        message_id=42,
        date=0,
    )
    cb = _FakeCallback(inaccessible)
    await _safe_edit(cb, "текст")
    assert len(cb.bot.sent) == 1


async def test_safe_edit_never_raises_when_sending_also_fails():
    class _BrokenBot:
        async def send_message(self, *args, **kwargs):
            raise RuntimeError("network down")

    cb = _FakeCallback(_FakeMessage(RuntimeError("boom")), bot=_BrokenBot())
    await _safe_edit(cb, "текст")  # не должно бросить
