from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import Base

if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite: busy_timeout=30s (connect_args timeout) чтобы конкурентные записи
    # ждали снятия блокировки вместо мгновенного "database is locked".
    async_engine = create_async_engine(
        settings.DATABASE_URL,
        connect_args={"timeout": 30},
    )

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
else:
    # Не-SQLite (напр. будущий Postgres) — без SQLite-специфичных настроек.
    async_engine = create_async_engine(settings.DATABASE_URL)

async_session = async_sessionmaker(async_engine, expire_on_commit=False)


async def init_db() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
