"""Страж стиля экосистемы (см. .superpowers/sdd/2026-09-27-ecosystem-style/texts-plan.md,
раздел 1): в текстах пользователю эмодзи нет, у кнопок — ровно одна иконка в начале.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bot import __main__ as entrypoint
from bot.db.free_quota import FreeQuota
from bot.handlers.info import paysupport_text, support_text, terms_text
from bot.handlers.user import (
    _help_text,
    _limit_reached_text,
    _status_text,
    _subscribe_text,
    _welcome_text,
)
from bot.keyboards.inline import (
    get_after_download_kb,
    get_back_to_menu_kb,
    get_help_kb,
    get_main_menu_kb,
    get_paywall_kb,
    get_status_kb,
    get_subscribe_kb,
)
from bot.services.download_queue import Admission
from bot.services.downloader import _ERROR_RULES
from bot.services.progress_texts import PREPARING_TEXT, QUEUED_TEXT, download_status_text, upload_status_text
from bot.services.queue_texts import interrupted_text, queue_status_text, refusal_text

# Диапазоны эмодзи из основных блоков Unicode плюс отдельно ℹ️ (Information
# Source + variation selector) — вне их остаются «→» (Arrows, U+2190–U+21FF)
# и шкала прогресса «▰▱» (Geometric Shapes, U+25A0–U+25FF).
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF⌀-⏿☀-➿⬀-⯿ℹ️]")

QUOTA_LEFT = FreeQuota(left=2, next_at=None)
QUOTA_EXHAUSTED = FreeQuota(left=0, next_at=datetime.now(timezone.utc) + timedelta(hours=1))


def _user_facing_texts() -> list[str]:
    texts = [
        _welcome_text(QUOTA_LEFT, has_subscription=False),
        _welcome_text(QUOTA_EXHAUSTED, has_subscription=False),
        _welcome_text(QUOTA_LEFT, has_subscription=True),
        _help_text(QUOTA_LEFT, has_subscription=False),
        _help_text(QUOTA_EXHAUSTED, has_subscription=False),
        _help_text(QUOTA_LEFT, has_subscription=True),
        _status_text(QUOTA_LEFT, None, 5),
        _status_text(QUOTA_EXHAUSTED, datetime.now(timezone.utc), 5),
        _limit_reached_text(QUOTA_EXHAUSTED),
        _subscribe_text(None),
        _subscribe_text(datetime.now(timezone.utc) + timedelta(days=10)),
        support_text(),
        paysupport_text(),
        terms_text(),
        PREPARING_TEXT,
        QUEUED_TEXT,
        download_status_text(None, limit_mb=1500),
        upload_status_text(None, size_mb=12.0, elapsed_sec=None, expected_sec=None, limit_mb=1500),
        queue_status_text(2),
        refusal_text(
            [("https://youtu.be/abc", Admission.DUPLICATE), ("https://youtu.be/def", Admission.FULL)],
            max_links=5,
        ),
        interrupted_text(["https://youtu.be/a"], refunded=True),
        interrupted_text(["https://youtu.be/a"], refunded=False),
    ]
    texts.extend(
        template.format(platform="YouTube", max_size="1,5 ГБ") for _name, _pattern, template in _ERROR_RULES
    )
    texts.extend(command.description for command in entrypoint.PUBLIC_COMMANDS)
    return texts


def test_no_emoji_in_user_facing_texts():
    for text in _user_facing_texts():
        assert not _EMOJI_RE.search(text), text


def _assert_single_leading_icon(text: str) -> None:
    match = _EMOJI_RE.match(text)
    assert match, f"кнопка без иконки в начале: {text!r}"
    rest = text[match.end():]
    assert rest.startswith(" ") and not rest.startswith("  "), (
        f"после иконки должен быть ровно один пробел: {text!r}"
    )
    assert not _EMOJI_RE.search(rest[1:]), f"эмодзи в тексте кнопки: {text!r}"


def _button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_user_keyboard_buttons_have_exactly_one_leading_icon():
    markups = [
        get_main_menu_kb(is_admin=False),
        get_back_to_menu_kb(is_admin=False),
        get_paywall_kb(is_admin=False),
        get_subscribe_kb("https://t.me/$invoice", 250, is_admin=False),
        get_status_kb(is_admin=False),
        get_help_kb(is_admin=False),
        get_after_download_kb(is_admin=False),
    ]
    for markup in markups:
        for text in _button_texts(markup):
            _assert_single_leading_icon(text)
