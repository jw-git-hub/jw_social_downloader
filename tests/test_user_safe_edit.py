from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Chat, InaccessibleMessage

from bot.handlers.user import _is_not_modified, _safe_edit


class _StubBot:
    """Fix round 1, п.5: этот стаб send_message НИКОГДА не падает, поэтому
    тесты ниже, использующие его, доказывают только "не пробрасывает
    исключение", а не "экран гарантированно доставлен". На проде
    `BadRequest: can't parse entities` — это ошибка в самом ТЕКСТЕ, и
    send_message с тем же текстом упадёт ровно так же: сработает finальный
    `except Exception` в `_safe_edit`, и пользователь не получит НИЧЕГО.
    Спеке это соответствует (бриф Task 26 требовал "не пробрасывать", а не
    "доставить любой ценой") — но не путать assert'ы ниже с гарантией
    доставки.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))


class _StubMessage:
    """Минимальный двойник Message: хендлеру нужны только chat и edit_text."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.chat = SimpleNamespace(id=4242)
        self.raises = raises
        self.edit_calls: list[str] = []
        self.answer_calls: list[str] = []

    async def edit_text(self, text, reply_markup=None):
        self.edit_calls.append(text)
        if self.raises is not None:
            raise self.raises

    async def answer(self, text, reply_markup=None):
        self.answer_calls.append(text)


class _StubCallback:
    def __init__(self, message) -> None:
        self.message = message
        self.bot = _StubBot()
        self.from_user = SimpleNamespace(id=777)


# TelegramAPIError.__init__(method, message) ничего не валидирует, method
# просто сохраняется — поэтому в тестах допустимо передать None.
def _bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=None, message=text)


def test_is_not_modified_recognises_telegram_wording():
    assert _is_not_modified(_bad_request("Bad Request: message is not modified")) is True
    assert _is_not_modified(_bad_request("Bad Request: can't parse entities")) is False


async def test_safe_edit_edits_when_message_is_editable():
    msg = _StubMessage()
    cb = _StubCallback(msg)

    await _safe_edit(cb, "новый текст")

    assert msg.edit_calls == ["новый текст"]
    assert cb.bot.sent == []


async def test_safe_edit_treats_not_modified_as_noop():
    msg = _StubMessage(raises=_bad_request("Bad Request: message is not modified"))
    cb = _StubCallback(msg)

    await _safe_edit(cb, "тот же текст")

    # Ни нового сообщения, ни дубля через answer().
    assert cb.bot.sent == []
    assert msg.answer_calls == []


async def test_safe_edit_sends_new_message_when_edit_is_rejected():
    msg = _StubMessage(raises=_bad_request("Bad Request: can't parse entities"))
    cb = _StubCallback(msg)

    await _safe_edit(cb, "экран меню")

    assert cb.bot.sent == [(4242, "экран меню")]


async def test_safe_edit_survives_missing_message():
    # callback.message == None: сообщение старше 48 часов.
    cb = _StubCallback(None)

    await _safe_edit(cb, "экран меню")

    assert cb.bot.sent == [(777, "экран меню")]


async def test_safe_edit_swallows_forbidden_error():
    msg = _StubMessage(raises=TelegramForbiddenError(method=None, message="bot was blocked"))
    cb = _StubCallback(msg)

    # Не должно бросить: юзер заблокировал бота — это штатный исход.
    await _safe_edit(cb, "экран меню")

    assert cb.bot.sent == []


async def test_safe_edit_does_not_leak_unexpected_errors():
    msg = _StubMessage(raises=RuntimeError("что-то совсем неожиданное"))
    cb = _StubCallback(msg)

    # Падение edit_text не должно мешать доставить экран новым сообщением.
    await _safe_edit(cb, "экран меню")

    assert cb.bot.sent == [(4242, "экран меню")]


async def test_safe_edit_never_treats_real_inaccessible_message_as_editable(monkeypatch):
    """Fix round 1, п.4: закрывает guard `not isinstance(msg, InaccessibleMessage)`.

    На настоящем aiogram 3.31.0 у `InaccessibleMessage` нет атрибута
    `edit_text` вовсе, поэтому даже БЕЗ guard'а `AttributeError` ловится
    общим `except Exception`, и экран всё равно доставляется фолбэком —
    внешнее поведение (что в итоге ушло пользователю) от guard'а не зависит,
    это подтверждено вручную и намеренно не проверяется этим тестом.
    Guard — про то, что код НИКОГДА не должен ПЫТАТЬСЯ вызвать edit_text на
    недоступном сообщении (корректность и чистота логов, не пользовательский
    результат). Чтобы отличить "никогда не пытается" от "пытается и ловит
    исключение", сюда подставлен исполняемый edit_text: без guard'а он был бы
    вызван и `_safe_edit` вернулся бы, не дойдя до `send_message`.
    """
    edit_mock = AsyncMock()
    monkeypatch.setattr(InaccessibleMessage, "edit_text", edit_mock, raising=False)
    inaccessible = InaccessibleMessage(chat=Chat(id=999, type="private"), message_id=1, date=0)
    cb = _StubCallback(inaccessible)

    await _safe_edit(cb, "экран меню")

    edit_mock.assert_not_called()
    assert cb.bot.sent == [(999, "экран меню")]
