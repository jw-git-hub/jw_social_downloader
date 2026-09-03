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

    model_config = {"env_file": ".env"}


settings = Settings()
