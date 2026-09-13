from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import Base

if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite: busy_timeout=30s (connect_args timeout) чтобы конкурентные записи
    # ждали снятия блокировки вместо мгновенного "database is locked".
    #
    # ВАЖНО (C-1, bot/db/queries.py: reserve_free_download): атомарность
    # резервирования квоты держится на том, что мы НЕ настраиваем здесь
    # "правильный" по рецепту SQLAlchemy режим изоляции pysqlite/aiosqlite
    # (isolation_level=None + явный BEGIN на событии "begin"). Используется
    # дефолтный легаси-режим (isolation_level=""), в котором SELECT не
    # открывает транзакцию — поэтому UPDATE в reserve_free_download всегда
    # оказывается ПЕРВЫМ оператором своей write-транзакции, и никакого окна
    # между чужим SELECT и своим UPDATE не остаётся.
    #
    # Если это когда-нибудь "исправить" по рецепту SQLAlchemy — проигрыш
    # гонки за последнюю единицу перестанет быть чистым `False` из
    # reserve_free_download и станет необработанным
    # `sqlite3.OperationalError: database is locked` (SQLITE_BUSY_SNAPSHOT),
    # на который busy_timeout выше НЕ распространяется (замерено). Двойного
    # списания при этом всё ещё не будет, но в хендлере вылетит исключение
    # вместо аккуратного False. Трогать isolation_level здесь — не просто
    # рефакторинг, а смена гарантий C-1.
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
