from bot import __main__ as entrypoint
from bot.config import settings


def test_session_timeout_comes_from_settings():
    session = entrypoint.build_session()
    assert session.timeout == settings.TELEGRAM_REQUEST_TIMEOUT


def test_session_timeout_outlives_the_server_idle_timeout():
    # У telegram-bot-api жёсткий IDLE_TIMEOUT=500 с. Соединение должен
    # закрывать сервер, а не мы, — иначе поведение непредсказуемо.
    assert entrypoint.build_session().timeout > 500
