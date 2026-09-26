from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.config import settings
from bot.db.free_quota import FreeQuota
from bot.handlers.user import (
    _help_text,
    _incoming_text,
    _limit_reached_text,
    _quota_line,
    _status_text,
    _welcome_text,
)

LIMIT = settings.FREE_DOWNLOADS_PER_DAY


def _exhausted(hours=5, minutes=12) -> FreeQuota:
    return FreeQuota(left=0, next_at=datetime.now(timezone.utc) + timedelta(hours=hours, minutes=minutes))


def test_quota_line_reads_as_remainder_for_the_day():
    line = _quota_line(FreeQuota(left=3, next_at=None), has_subscription=False)
    assert f"осталось <b>3 из {LIMIT}</b> на сутки" in line
    assert "3/3" not in line


def test_quota_line_for_exhausted_user_says_when_next_opens():
    line = _quota_line(_exhausted(), has_subscription=False)
    assert "закончились" in line
    assert "через <b>5 ч 12 мин</b>" in line
    assert "подпиской" in line


def test_quota_line_for_subscriber_ignores_free_counter():
    line = _quota_line(_exhausted(), has_subscription=True)
    assert "Подписка активна" in line
    assert "бесплатн" not in line.lower()


def test_welcome_shows_actual_remainder():
    text = _welcome_text(FreeQuota(left=1, next_at=None), has_subscription=False)
    assert f"1 из {LIMIT}" in text


def test_welcome_and_help_do_not_promise_seconds():
    full = FreeQuota(left=3, next_at=None)
    for text in (
        _welcome_text(full, False),
        _welcome_text(full, True),
        _help_text(full, False),
        _help_text(full, True),
    ):
        assert "за секунды" not in text
        assert "несколько секунд" not in text


def test_status_text_uses_remainder_wording():
    text = _status_text(FreeQuota(left=2, next_at=None), None, 7)
    assert f"осталось <b>2 из {LIMIT}</b> на сутки" in text
    assert "❌ Не активна" in text
    assert "<b>7</b>" in text


def test_status_text_shows_wait_when_exhausted():
    text = _status_text(_exhausted(hours=3, minutes=0), None, 0)
    assert "через <b>3 ч</b>" in text


def test_status_text_renders_active_subscription_date():
    until = datetime.now(timezone.utc) + timedelta(days=3)
    text = _status_text(FreeQuota(left=0, next_at=None), until, 0)
    assert until.strftime("%d.%m.%Y") in text


def test_limit_reached_text_says_when_and_offers_subscription():
    text = _limit_reached_text(_exhausted())
    assert "закончились" in text
    assert "через <b>5 ч 12 мин</b>" in text
    assert "подпиской" in text


def test_incoming_text_prefers_text_then_caption():
    assert _incoming_text(SimpleNamespace(text="привет", caption=None)) == "привет"
    assert (
        _incoming_text(SimpleNamespace(text=None, caption="смотри https://vt.tiktok.com/abc"))
        == "смотри https://vt.tiktok.com/abc"
    )
    assert _incoming_text(SimpleNamespace(text=None, caption=None)) == ""
