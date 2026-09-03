def test_new_transport_settings_have_expected_defaults():
    from bot.config import settings

    assert settings.TELEGRAM_API_BASE == "http://127.0.0.1:8081"
    assert settings.TELEGRAM_REQUEST_TIMEOUT == 900
    assert settings.MIN_FREE_DISK_GB == 5
    assert settings.CLEANUP_MAX_AGE_MIN == 45


def test_size_and_timeout_raised_for_local_api():
    from bot.config import settings

    # Стартовое значение; поднимается до 1900 только после замера (Задача 13).
    assert settings.MAX_FILE_SIZE_MB == 1500
    # 2 ГБ на ~10 МБ/с не укладываются в прежние 120 секунд.
    assert settings.DOWNLOAD_TIMEOUT == 900


def test_request_timeout_exceeds_server_idle_timeout():
    from bot.config import settings

    # У telegram-bot-api жёсткий IDLE_TIMEOUT=500 с. Наш клиентский таймаут
    # должен быть заведомо больше, чтобы мы наблюдали закрытие сервером,
    # а не собственный таймаут.
    assert settings.TELEGRAM_REQUEST_TIMEOUT > 500
