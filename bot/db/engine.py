from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import EXTRA_INDEX_DDL, USERNAME_UNIQUE_DDL, Base, DownloadLog

# Журнал загрузок хранит полные URL с приватными пер-шаринговыми токенами
# (`?stkn=`, `?igsh=`). Порог заведомо больше самого длинного окна статистики
# (30 дней в get_stats), чтобы чистка не искажала цифры.
DOWNLOAD_LOG_RETENTION_DAYS = 180


def apply_sqlite_pragmas(dbapi_conn) -> None:
    """PRAGMA, обязательные на КАЖДОМ соединении SQLite.

    Отдельная функция (а не инлайн в листенере), чтобы применять ровно те же
    настройки и в бою (ниже), и в тестовой фикстуре `db_session`
    (`tests/conftest.py`) — тест на `PRAGMA foreign_keys` без этого проверял
    бы фикстуру, а не боевой код.

    `foreign_keys` SQLite по умолчанию не проверяет: без него строки
    `download_log` переживают удаление пользователя сиротами и попадают
    в статистику. PRAGMA действует только на НОВЫЕ операции с момента
    включения — уже существующие осиротевшие строки (если такие есть)
    включение не ревалидирует и не находит само по себе.
    """
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


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
    #
    # `apply_sqlite_pragmas` ниже (foreign_keys=ON в т.ч.) на это не влияет:
    # PRAGMA не открывает и не заменяет транзакцию, о которую держится C-1.
    async_engine = create_async_engine(
        settings.DATABASE_URL,
        connect_args={"timeout": 30},
    )

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        apply_sqlite_pragmas(dbapi_conn)
else:
    # Не-SQLite (напр. будущий Postgres) — без SQLite-специфичных настроек.
    async_engine = create_async_engine(settings.DATABASE_URL)

async_session = async_sessionmaker(async_engine, expire_on_commit=False)


async def _purge_old_download_logs() -> None:
    """Ретеншен журнала загрузок. Строки не удалял никто."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DOWNLOAD_LOG_RETENTION_DAYS)
    try:
        async with async_engine.begin() as conn:
            result = await conn.execute(delete(DownloadLog).where(DownloadLog.created_at < cutoff))
        if result.rowcount:
            logger.info("Download log retention: removed {} row(s)", result.rowcount)
    except Exception as exc:
        logger.warning("Download log retention failed: {}", exc)


async def init_db() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in EXTRA_INDEX_DDL:
            await conn.execute(text(statement))

    # Отдельной транзакцией: неудача здесь не должна откатывать индексы выше
    # и не должна ронять старт бота.
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text(USERNAME_UNIQUE_DDL))
    except Exception as exc:
        logger.error(
            "Уникальный индекс по users.username не создан — в базе есть дубликаты ников. "
            "Запустить scripts/migrate_20260913.py. ({})",
            exc,
        )

    await _purge_old_download_logs()
