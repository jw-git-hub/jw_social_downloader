import os

# Должно стоять ДО любого импорта из bot.* — bot/config.py инстанцирует
# Settings() на импорте и падает без этих переменных.
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("DOWNLOAD_ROOT", "/tmp/jw_test_downloads")

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db_session(tmp_path):
    """Чистая БД на каждый тест: временный SQLite со схемой из моделей.

    Собственный движок, а не `bot.db.engine.async_session`: тот создаётся на
    импорте из `settings.DATABASE_URL` и указывает на рабочую базу.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bot.db.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def make_user():
    """Фабрика пользователей. Telegram-id заведомо выдуманные."""
    from bot.db.models import User

    def _make(user_id: int = 1000000001, **overrides):
        fields = {
            "id": user_id,
            "username": "tester",
            "full_name": "Test User",
            "free_downloads_left": 3,
            "subscription_until": None,
            "is_banned": False,
            "total_downloads": 0,
        }
        fields.update(overrides)
        return User(**fields)

    return _make
