import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def test_max_file_size_env_var_overrides_the_code_default(monkeypatch):
    """Предохранитель (владелец, 2026-09-15): пока транспорт — облачный Bot
    API с потолком 50 МБ, .env на хосте временно выставляет
    MAX_FILE_SIZE_MB=50 поверх целевого дефолта 1500 из bot/config.py
    (тот дефолт поднят заранее под Tasks 12/13 и пином отдельным тестом
    test_size_and_timeout_raised_for_local_api — трогать его нельзя).

    .env — вне git и не копируется в тестовый образ (.dockerignore), поэтому
    сам файл здесь не проверить. Но docker-compose подаёт его строки как
    ОБЫЧНЫЕ переменные окружения процесса (env_file), и именно этот механизм
    здесь проверяется по факту прогона Settings, а не предполагается."""
    monkeypatch.delenv("MAX_FILE_SIZE_MB", raising=False)
    from bot.config import Settings

    assert Settings().MAX_FILE_SIZE_MB == 1500

    monkeypatch.setenv("MAX_FILE_SIZE_MB", "50")
    assert Settings().MAX_FILE_SIZE_MB == 50


def test_cloud_api_safety_valve_stays_until_local_bot_api_migration():
    """Тройной предохранитель Task 2/12/13 на время переходного периода:
    MAX_FILE_SIZE_MB в коде уже поднят до 1500 (целевое значение под
    локальный telegram-bot-api), но транспорт всё ещё ОБЛАЧНЫЙ Bot API с
    жёстким потолком 50 МБ. Пока bot/__main__.py не строит клиента к
    локальному серверу (TelegramAPIServer / Bot(..., api=...) — Tasks
    12/13), .env обязан держать временное значение 50, и это отражено в
    .env.example.

    Тест ЕСТЕСТВЕННО покраснеет, когда Tasks 12/13 добавят локальный Bot API
    в __main__.py — это СИГНАЛ снять предохранитель (вернуть .env
    MAX_FILE_SIZE_MB на 1500) и обновить/удалить сам тест, а не поломка,
    которую нужно чинить в коде.
    """
    main_source = (ROOT / "bot" / "__main__.py").read_text(encoding="utf-8")
    migrated = "TelegramAPIServer" in main_source or re.search(r"\bapi\s*=\s*\w", main_source)
    assert not migrated, (
        "локальный Bot API уже подключен в __main__.py — самое время убрать "
        "временный предохранитель MAX_FILE_SIZE_MB=50 из .env (вернуть 1500) "
        "и актуализировать/удалить этот тест"
    )

    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MAX_FILE_SIZE_MB=50" in example, (
        "временное значение предохранителя (50) должно быть видно в "
        ".env.example, пока транспорт — облачный Bot API"
    )
