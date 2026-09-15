from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.config import settings
from bot.handlers.user import (
    _help_text,
    _incoming_text,
    _quota_line,
    _status_text,
    _welcome_text,
)


def test_quota_line_reads_as_remainder_not_usage():
    # «Бесплатных скачиваний: 3/3» читалось как «использовано 3 из 3».
    line = _quota_line(3, has_subscription=False)

    assert "Осталось бесплатных" in line
    assert f"3 из {settings.FREE_DOWNLOADS}" in line
    assert "3/3" not in line


def test_quota_line_for_exhausted_user_promises_nothing():
    line = _quota_line(0, has_subscription=False)

    assert "Осталось бесплатных" not in line
    assert "закончились" in line


def test_quota_line_for_subscriber_ignores_free_counter():
    line = _quota_line(0, has_subscription=True)

    assert "Подписка активна" in line
    assert "бесплатн" not in line.lower()


def test_welcome_shows_actual_remainder_not_hardcoded_three():
    text = _welcome_text(1, has_subscription=False)

    assert f"1 из {settings.FREE_DOWNLOADS}" in text
    assert "<b>3 бесплатных</b>" not in text


def test_welcome_and_help_do_not_promise_seconds():
    for text in (
        _welcome_text(3, False),
        _welcome_text(0, True),
        _help_text(3, False),
        _help_text(0, True),
    ):
        assert "за секунды" not in text
        assert "несколько секунд" not in text


def test_status_text_uses_remainder_wording():
    text = _status_text(2, False, None, 7)

    assert f"Осталось бесплатных: <b>2 из {settings.FREE_DOWNLOADS}</b>" in text
    assert "❌ Не активна" in text
    assert "<b>7</b>" in text


def test_status_text_renders_active_subscription_date():
    until = datetime.now(timezone.utc) + timedelta(days=3)

    text = _status_text(0, True, until, 0)

    assert until.strftime("%d.%m.%Y") in text


def test_incoming_text_prefers_text_then_caption():
    assert _incoming_text(SimpleNamespace(text="привет", caption=None)) == "привет"
    assert (
        _incoming_text(SimpleNamespace(text=None, caption="смотри https://vt.tiktok.com/abc"))
        == "смотри https://vt.tiktok.com/abc"
    )
    assert _incoming_text(SimpleNamespace(text=None, caption=None)) == ""
