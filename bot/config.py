import os
from typing import Mapping

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_ID: int
    ADMIN_USERNAME: str = "@your_admin_username"
    DATABASE_URL: str = "sqlite+aiosqlite:///data/bot.db"
    # Платёжные реквизиты берутся из .env (см. .env.example). Пустые значения по
    # умолчанию — чтобы в репозитории не было реальных кошельков/счетов.
    USDT_TRC20_ADDRESS: str = ""
    VN_BANK_DETAILS: str = ""
    TH_BANK_DETAILS: str = ""
    FREE_DOWNLOADS: int = 3
    MAX_FILE_SIZE_MB: int = 1500
    DOWNLOAD_TIMEOUT: int = 900
    COOKIES_FILE: str = ""
    TIKTOK_PROXY: str = ""  # опционально: http(s)/socks-прокси для обхода анти-бота TikTok; пусто = напрямую

    # ── Локальный Bot API ──
    # Адрес самостоятельно поднятого telegram-bot-api (см. docker-compose.yml).
    TELEGRAM_API_BASE: str = "http://127.0.0.1:8081"
    # Таймаут HTTP-запроса к нему. Заведомо больше серверного IDLE_TIMEOUT=500,
    # чтобы соединение закрывал сервер, а не мы — так поведение предсказуемо.
    TELEGRAM_REQUEST_TIMEOUT: int = 900
    # Ключи приложения с my.telegram.org для контейнера telegram-bot-api.
    # Сам бот их не использует, но объявлены явно: так они перестают быть
    # «посторонними» для источника dotenv и попадают под проверку опечаток.
    TELEGRAM_API_ID: str = ""
    TELEGRAM_API_HASH: str = ""

    # ── Файлы ──
    # Папка загрузок. Общая с контейнером telegram-bot-api по ОДИНАКОВОМУ пути:
    # бот отдаёт файлы ссылкой file://, и сервер должен разрешить тот же путь.
    DOWNLOAD_ROOT: str = "/srv/jw_downloads"
    # Ниже этого порога свободного места загрузку не начинаем.
    MIN_FREE_DISK_GB: int = 5
    # Возраст, после которого подметальщик считает папку осиротевшей.
    CLEANUP_MAX_AGE_MIN: int = 45

    SUBSCRIPTION_PRICE_USDT: int = 5
    SUBSCRIPTION_PRICE_VND: int = 125000
    SUBSCRIPTION_PRICE_THB: int = 175

    # extra="ignore" остаётся: в окружении осознанно лежат ключи, которых
    # Settings не объявляет, а источник dotenv подаёт в валидацию ВСЕ непустые
    # ключи файла. Опечатки в СВОИХ ключах ловит check_env_keys ниже.
    model_config = {"env_file": ".env", "extra": "ignore"}


def _within_one_edit(a: str, b: str) -> bool:
    """True, если строки различаются не более чем одной правкой символа."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1

    short, long_ = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long_):
        if short[i] == long_[j]:
            i += 1
            j += 1
            continue
        if skipped:
            return False
        skipped = True
        j += 1
    return True


def check_env_keys(environ: Mapping[str, str] | None = None) -> list[str]:
    """Ловит опечатки в именах ключей конфигурации.

    `extra="ignore"` необходим, но он же превратил опечатку в имени денежного
    ключа из падения на импорте в молчаливую недонастройку: `USDT_TRC2O_ADDRESS`
    просто игнорируется, и пользователи видят пустой адрес оплаты.

    Ругаться на все посторонние переменные окружения нельзя — в контейнере их
    сотни. Поэтому ловим только те, что отличаются от объявленного поля ровно
    одной правкой символа: это и есть определение опечатки. Проверяем именно
    окружение, а не файл `.env`: compose подаёт ключи через `env_file`, то есть
    переменными окружения, а самого файла в образе нет.

    Возвращает пустой список: непохожие переменные — не наша забота. Опечатка
    же поднимает ValueError, потому что молчаливо неверный адрес оплаты стоит
    денег, а лишний перезапуск с исправленным именем — нет.
    """
    source = os.environ if environ is None else environ
    known = set(Settings.model_fields)
    typos = [
        (key, near)
        for key in source
        if key not in known
        for near in (next((k for k in known if _within_one_edit(key, k)), None),)
        if near is not None
    ]
    if typos:
        details = ", ".join(f"{bad} → похоже на {good}" for bad, good in typos)
        raise ValueError(f"Опечатка в именах ключей конфигурации: {details}")
    return []


settings = Settings()
check_env_keys()
