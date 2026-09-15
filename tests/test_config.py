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


def test_unknown_dotenv_keys_do_not_break_settings(tmp_path, monkeypatch):
    # В .env лежат TELEGRAM_API_ID/TELEGRAM_API_HASH для контейнера
    # telegram-bot-api. Settings их не объявляет, и без extra="ignore"
    # источник dotenv роняет валидацию на импорте.
    #
    # conftest.py делает os.environ.setdefault("BOT_TOKEN", ...) и
    # ("ADMIN_ID", ...) на уровне модуля. Переменные окружения имеют приоритет
    # над dotenv-источником в pydantic-settings, поэтому их нужно снять здесь,
    # иначе Settings(_env_file=...) прочитает значения не из временного файла,
    # а из окружения, и проверка ничего не докажет про сам dotenv-источник.
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_ID", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_TOKEN=123:ABC\n"
        "ADMIN_ID=1\n"
        "TELEGRAM_API_ID=1234567\n"
        "TELEGRAM_API_HASH=deadbeefdeadbeefdeadbeefdeadbeef\n"
    )

    from bot.config import Settings

    loaded = Settings(_env_file=str(env_file))
    assert loaded.BOT_TOKEN == "123:ABC"


def test_download_root_default_matches_the_shared_mount_path():
    # Этот путь обязан совпадать с bind-монтированием контейнера
    # telegram-bot-api, иначе отдача по file:// не разрешится на его стороне.
    from bot.config import Settings

    assert Settings.model_fields["DOWNLOAD_ROOT"].default == "/srv/jw_downloads"


def test_downloader_and_cleanup_use_the_configured_download_root():
    # DOWNLOAD_DIR в обоих модулях обязан читаться из settings.DOWNLOAD_ROOT,
    # а не быть захардкожен отдельно — иначе они снова могут разъехаться
    # (как это было с /tmp/jw_downloads, который физически не совпадал с
    # каталогом, ожидаемым telegram-bot-api).
    from pathlib import Path

    import bot.services.cleanup as cleanup
    import bot.services.downloader as downloader
    from bot.config import settings

    assert downloader.DOWNLOAD_DIR == Path(settings.DOWNLOAD_ROOT)
    assert cleanup.DOWNLOAD_DIR == Path(settings.DOWNLOAD_ROOT)
