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
    MAX_FILE_SIZE_MB: int = 50
    DOWNLOAD_TIMEOUT: int = 120
    COOKIES_FILE: str = ""
    TIKTOK_PROXY: str = ""  # опционально: http(s)/socks-прокси для обхода анти-бота TikTok; пусто = напрямую
    SUBSCRIPTION_PRICE_USDT: int = 5
    SUBSCRIPTION_PRICE_VND: int = 125000
    SUBSCRIPTION_PRICE_THB: int = 175

    model_config = {"env_file": ".env"}


settings = Settings()
