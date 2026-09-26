from bot.config import settings
from bot.handlers.info import paysupport_text, support_text, terms_text


def test_terms_names_price_renewal_refund_rule_and_contacts(monkeypatch):
    monkeypatch.setattr(settings, "SUBSCRIPTION_PRICE_STARS", 250)
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@owner")
    text = terms_text()
    assert "250 ⭐" in text
    assert "3 скачивания за любые 24 часа" in text
    assert "автоматически" in text
    assert "Мои звёзды" in text
    assert "/paysupport" in text and "/support" in text and "@owner" in text
    assert "администратор" in text


def test_paysupport_points_to_admin_and_terms(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@owner")
    text = paysupport_text()
    assert "@owner" in text and "/terms" in text and "дату платежа" in text


def test_texts_escape_admin_username(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@a<b>&c")
    for text in (support_text(), paysupport_text(), terms_text()):
        assert "@a&lt;b&gt;&amp;c" in text
        assert "<b>&c" not in text


def test_support_text_no_longer_mentions_payment_screenshot():
    assert "скриншот" not in support_text().lower()
