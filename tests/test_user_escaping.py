from bot.config import settings
from bot.handlers.user import (
    _download_failed_text,
    _media_caption,
    _payment_details_text,
    _support_text,
)


def test_payment_details_escape_ampersand_and_brackets(monkeypatch):
    monkeypatch.setattr(settings, "VN_BANK_DETAILS", "NGUYEN VAN A & CO <VCB>")
    monkeypatch.setattr(settings, "USDT_TRC20_ADDRESS", "T<addr>")
    monkeypatch.setattr(settings, "TH_BANK_DETAILS", "K & Bank")

    text = _payment_details_text()

    assert "NGUYEN VAN A &amp; CO &lt;VCB&gt;" in text
    assert "T&lt;addr&gt;" in text
    assert "K &amp; Bank" in text
    # Сырых спецсимволов из значений в разметке остаться не должно.
    assert "<VCB>" not in text
    assert "& CO" not in text


def test_payment_details_survive_empty_settings(monkeypatch):
    # Дефолты в репозитории пустые — экран обязан открываться и так.
    monkeypatch.setattr(settings, "VN_BANK_DETAILS", "")
    monkeypatch.setattr(settings, "USDT_TRC20_ADDRESS", "")
    monkeypatch.setattr(settings, "TH_BANK_DETAILS", "")

    text = _payment_details_text()

    assert "<code></code>" in text


def test_support_text_escapes_admin_username(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@admin<b>")

    text = _support_text()

    assert "@admin&lt;b&gt;" in text
    assert "@admin<b>" not in text


def test_download_failed_text_does_not_escape_twice():
    # Загрузчик уже экранировал stderr (downloader.py:77, :443).
    already_escaped = "<code>ERROR: &lt;html&gt; not found</code>"

    text = _download_failed_text(already_escaped)

    assert already_escaped in text
    assert "&amp;lt;" not in text


def test_download_failed_text_handles_missing_message():
    text = _download_failed_text(None)

    assert "None" not in text
    assert "Не удалось скачать" in text


def test_media_caption_escapes_platform():
    assert _media_caption("tiktok", "video") == "✅ Видео из Tiktok"
    assert _media_caption("pinterest", "image") == "✅ Фото из Pinterest"
    assert _media_caption("in<s>ta", "video") == "✅ Видео из In&lt;s&gt;ta"
