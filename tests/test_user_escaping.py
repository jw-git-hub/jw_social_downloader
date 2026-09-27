from bot.config import settings
from bot.handlers.info import support_text
from bot.handlers.user import _download_failed_text, _media_caption


def test_support_text_escapes_admin_username(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@admin<b>")

    text = support_text()

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
    assert _media_caption("tiktok", "video") == "Видео из TikTok"
    assert _media_caption("pinterest", "image") == "Фото из Pinterest"
    assert _media_caption("in<s>ta", "video") == "Видео из In&lt;s&gt;ta"
