from aiogram.client.telegram import PRODUCTION

from bot import __main__ as entrypoint
from bot.config import settings


def test_cloud_transport_uses_production_server_by_default():
    session = entrypoint.build_session()

    assert session.api is PRODUCTION


def test_local_transport_points_at_the_configured_server(monkeypatch):
    monkeypatch.setattr(settings, "USE_LOCAL_BOT_API", True)

    session = entrypoint.build_session()

    assert (
        session.api.api_url(token="123:ABC", method="sendVideo")
        == f"{settings.TELEGRAM_API_BASE}/bot123:ABC/sendVideo"
    )
    assert session.api.is_local is True


def test_cloud_transport_keeps_the_configured_request_timeout(monkeypatch):
    monkeypatch.setattr(settings, "USE_LOCAL_BOT_API", False)

    assert entrypoint.build_session().timeout == settings.TELEGRAM_REQUEST_TIMEOUT


def test_local_transport_keeps_the_configured_request_timeout(monkeypatch):
    monkeypatch.setattr(settings, "USE_LOCAL_BOT_API", True)

    assert entrypoint.build_session().timeout == settings.TELEGRAM_REQUEST_TIMEOUT
