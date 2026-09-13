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


def _prod_like_sqlite_engine(db_path):
    """Движок SQLite с той же конфигурацией блокировок, что и боевой
    `bot/db/engine.py`: `connect_args={"timeout": 30}` + PRAGMA из
    `apply_sqlite_pragmas` (WAL, `synchronous=NORMAL`, `foreign_keys=ON`).

    Тест на гонку поверх движка БЕЗ этих настроек проверяет другую
    семантику блокировок, чем прод, и ничего не доказывает про боевой код
    — поэтому это не инлайн в фикстуре, а отдельная фабрика: `db_session`
    берёт из неё одно соединение, тесты на гонку (`test_quota_queries.py`)
    — несколько независимых, указывающих на один и тот же файл.

    PRAGMA берутся из `bot.db.engine.apply_sqlite_pragmas`, а не
    продублированы здесь: иначе фикстура и боевой код могли бы разойтись
    молча (например, кто-то включит `foreign_keys` в одном месте и забудет
    про другое) — `test_foreign_keys_pragma_is_on` проверяет ровно этот
    боевой листенер, а не копию его логики.
    """
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine

    from bot.db.engine import apply_sqlite_pragmas

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        apply_sqlite_pragmas(dbapi_conn)

    return engine


@pytest.fixture
async def db_session(tmp_path):
    """Чистая БД на каждый тест: временный SQLite со схемой из моделей,
    движок — прод-конфигурации (см. `_prod_like_sqlite_engine`).

    Собственный движок, а не `bot.db.engine.async_session`: тот создаётся на
    импорте из `settings.DATABASE_URL` и указывает на рабочую базу.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from bot.db.models import EXTRA_INDEX_DDL, Base

    engine = _prod_like_sqlite_engine(tmp_path / "test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # `create_all` не заводит EXTRA_INDEX_DDL (это же верно и в проде —
        # весь смысл отдельного DDL в том, что create_all его не делает).
        # Без этой строки тестовая схема структурно расходилась с боевой:
        # ни один новый индекс в тестах не создавался, только то, что есть
        # в metadata моделей — задачи 21 (фикс-раунд 1).
        for statement in EXTRA_INDEX_DDL:
            await conn.execute(text(statement))

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def sqlite_engine_factory():
    """Фабрика доп. движков той же прод-конфигурации, что и `db_session`.

    `db_session` даёт одно соединение. Тестам на гонку нужно несколько
    независимых соединений к ОДНОМУ файлу (`tmp_path` — тот же экземпляр,
    что видит `db_session` в этом же тесте): `engine = sqlite_engine_factory(tmp_path / "test.db")`.
    Схему создавать заново не нужно, если файл уже проинициализирован через
    `db_session`; вызывающий отвечает за `await engine.dispose()`.
    """
    return _prod_like_sqlite_engine


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
