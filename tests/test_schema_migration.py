import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot.db.models import EXTRA_INDEX_DDL, USERNAME_UNIQUE_DDL

ROOT = Path(__file__).resolve().parent.parent


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migrate_20260913", ROOT / "scripts" / "migrate_20260913.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_db(path: Path) -> sqlite3.Connection:
    """Схема без индексов — такая, какая лежит в боевом томе."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username VARCHAR(255),
            full_name VARCHAR(255) NOT NULL,
            free_downloads_left INTEGER NOT NULL DEFAULT 3,
            subscription_until DATETIME,
            is_banned BOOLEAN NOT NULL DEFAULT 0,
            total_downloads INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        CREATE TABLE download_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            url TEXT NOT NULL,
            platform VARCHAR(32) NOT NULL,
            status VARCHAR(16) NOT NULL,
            file_size_mb FLOAT,
            created_at DATETIME NOT NULL
        );
        """
    )
    return conn


def test_extra_ddl_is_idempotent(tmp_path):
    conn = _legacy_db(tmp_path / "bot.db")
    for statement in EXTRA_INDEX_DDL:
        conn.execute(statement)
        conn.execute(statement)  # второй прогон не должен падать
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_download_log_created_at" in names
    assert "ix_download_log_user_id" in names


def test_unique_index_refuses_duplicates_after_migration(tmp_path):
    conn = _legacy_db(tmp_path / "bot.db")
    conn.executescript(
        """
        INSERT INTO users VALUES (1000000001,'foo','A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO users VALUES (1000000002,'Foo','B',3,NULL,0,0,'2025-01-01','2026-09-01');
        INSERT INTO users VALUES (1000000003,NULL,'C',3,NULL,0,0,'2025-01-01','2025-01-01');
        """
    )
    conn.commit()

    # До миграции уникальный индекс создать нельзя — это и есть повод для скрипта.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(USERNAME_UNIQUE_DDL)

    migration = _load_migration()
    cleared = migration.dedupe_usernames(conn)
    conn.commit()

    assert cleared == [(1000000001, "foo")]
    conn.execute(USERNAME_UNIQUE_DDL)  # теперь проходит

    # У более свежей строки ник сохранён, у старой снят, NULL не тронут.
    rows = dict(conn.execute("SELECT id, username FROM users"))
    assert rows == {1000000001: None, 1000000002: "Foo", 1000000003: None}


def test_unique_index_allows_many_nulls(tmp_path):
    conn = _legacy_db(tmp_path / "bot.db")
    conn.execute(USERNAME_UNIQUE_DDL)
    conn.executescript(
        """
        INSERT INTO users VALUES (1000000001,NULL,'A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO users VALUES (1000000002,NULL,'B',3,NULL,0,0,'2025-01-01','2025-01-01');
        """
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


def test_unique_index_is_case_insensitive(tmp_path):
    conn = _legacy_db(tmp_path / "bot.db")
    conn.execute(USERNAME_UNIQUE_DDL)
    conn.execute("INSERT INTO users VALUES (1000000001,'foo','A',3,NULL,0,0,'2025-01-01','2025-01-01')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO users VALUES (1000000002,'FOO','B',3,NULL,0,0,'2025-01-01','2025-01-01')")


async def test_foreign_keys_pragma_is_on(db_session):
    from sqlalchemy import text

    value = await db_session.scalar(text("PRAGMA foreign_keys"))
    assert value == 1


# ─────────────────────────────────────────────────────────────────────────
# Дополнительные проверки поверх плана задачи (решения контроллера):
#
# 1) "индекс создан" != "индекс используется планировщиком" — тесты выше
#    (test_extra_ddl_is_idempotent) проверяют только наличие имени в
#    sqlite_master. Ниже — EXPLAIN QUERY PLAN на РЕАЛЬНЫХ формах запросов
#    из bot/db/queries.py, включая находку: func.lower(User.username) не
#    использует COLLATE NOCASE-индекс, понадобился отдельный индекс по
#    выражению lower(username) (см. models.py).
# 2) Семантика PRAGMA foreign_keys=ON на существующих данных (решение 5).
# 3) Безопасное по умолчанию поведение scripts/migrate_20260913.py:
#    обязательный бэкап, дубликаты и ретеншен не трогаются без явных флагов
#    (решения 2-4).
# ─────────────────────────────────────────────────────────────────────────


def test_username_lower_index_is_used_by_the_real_lookup_query(tmp_path):
    """ix_users_username_lower должен реально выбираться планировщиком под
    ТОТ ЖЕ запрос, что делает get_user_by_username (`func.lower(User.username)
    == needle`, с ORDER BY/LIMIT). Уникальный NOCASE-индекс (USERNAME_UNIQUE_DDL)
    для этой формы запроса не подходит: `COLLATE NOCASE` и `lower(...)` —
    разные выражения, планировщик их не отождествляет (проверено отдельно
    через EXPLAIN QUERY PLAN на голом sqlite3 при разработке этой задачи).
    Без этого индекса поиск по нику остаётся полным сканированием даже
    после того, как уникальный индекс создан.
    """
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.executescript(
        "\n".join(
            f"INSERT INTO users VALUES ({i},'user{i}','N{i}',3,NULL,0,0,"
            f"'2025-01-01','2025-01-01');"
            for i in range(1, 301)
        )
    )
    conn.commit()
    for statement in EXTRA_INDEX_DDL:
        conn.execute(statement)
    conn.execute(USERNAME_UNIQUE_DDL)
    conn.commit()
    conn.execute("ANALYZE")

    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_users_username_lower" in names
    assert "ix_users_subscription_until" in names

    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM users WHERE lower(username) = ? "
        "ORDER BY updated_at DESC, id DESC LIMIT 1",
        ("user123",),
    ).fetchall()
    plan_text = " ".join(row[-1] for row in plan)
    assert "ix_users_username_lower" in plan_text, plan_text
    assert "SCAN" not in plan_text, plan_text


def test_download_log_indexes_are_used_by_planner(tmp_path):
    """Аналогично: created_at/user_id индексы должны реально выбираться под
    формы запросов get_stats (COUNT(*) ... created_at >= cutoff) и выборки
    по пользователю, а не только присутствовать в sqlite_master.
    """
    # 30 разных пользователей по 10 строк лога каждому: `user_id = ?` должен
    # быть избирательным (1/30 таблицы). Изначальная версия этого теста
    # держала все 300 строк на ОДНОМ user_id — планировщик КОРРЕКТНО выбирал
    # SCAN (индекс без пользы, когда предикат не отсеивает почти ничего), и
    # тест на самом деле проверял не то, что заявлял.
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.executescript(
        "\n".join(
            f"INSERT INTO users VALUES ({u},'user{u}','N{u}',3,NULL,0,0,"
            f"'2025-01-01','2025-01-01');"
            for u in range(1, 31)
        )
    )
    conn.executescript(
        "\n".join(
            f"INSERT INTO download_log VALUES ({i},{1 + i % 30},'https://example.com/{i}',"
            f"'instagram','ok',1.0,'2025-{1 + i % 12:02d}-{1 + i % 27:02d} 00:00:00');"
            for i in range(1, 301)
        )
    )
    conn.commit()
    for statement in EXTRA_INDEX_DDL:
        conn.execute(statement)
    conn.commit()
    conn.execute("ANALYZE")

    plan_created_at = conn.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM download_log WHERE created_at >= ?",
        ("2025-06-01 00:00:00",),
    ).fetchall()
    text_created_at = " ".join(row[-1] for row in plan_created_at)
    assert "ix_download_log_created_at" in text_created_at, text_created_at

    plan_user_id = conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM download_log WHERE user_id = ?", (1,)
    ).fetchall()
    text_user_id = " ".join(row[-1] for row in plan_user_id)
    assert "ix_download_log_user_id" in text_user_id, text_user_id


def test_foreign_keys_pragma_blocks_new_orphans_but_ignores_existing(tmp_path):
    """PRAGMA foreign_keys=ON проверяет только НОВЫЕ операции с момента
    включения. Строка, осиротевшая ДО включения (как декоративный FK в
    текущем проде это допускает), никуда не девается сама по себе и не
    мешает базе открыться — а вот вставить НОВУЮ такую же строку после
    включения уже нельзя. На копии боевой БД (см. отчёт задачи) сирот на
    момент ревизии нет, так что сценарий воспроизведён синтетически.
    """
    from bot.db.engine import apply_sqlite_pragmas

    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.execute("INSERT INTO users VALUES (1,'ivan','A',3,NULL,0,0,'2025-01-01','2025-01-01')")
    # Сирота, вставленная ДО включения проверки FK — ровно то, что декоративный
    # FK в текущем проде допускает.
    conn.execute(
        "INSERT INTO download_log VALUES (1,999999,'https://example.com/x',"
        "'instagram','ok',1.0,'2025-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    conn2 = sqlite3.connect(db_path)
    apply_sqlite_pragmas(conn2)
    assert conn2.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    # Существующая сирота осталась на месте — включение PRAGMA её не находит.
    assert (
        conn2.execute("SELECT COUNT(*) FROM download_log WHERE user_id = 999999").fetchone()[0]
        == 1
    )

    # Но новую такую же строку вставить уже нельзя.
    with pytest.raises(sqlite3.IntegrityError):
        conn2.execute(
            "INSERT INTO download_log VALUES (2,888888,'https://example.com/y',"
            "'instagram','ok',1.0,'2025-01-01 00:00:00')"
        )
    conn2.close()


def test_retention_days_constant_matches_engine():
    """Локальная константа в скрипте не импортируется из bot.db.engine
    (см. комментарий в migrate_20260913.py), поэтому от рассинхронизации
    защищает только этот тест."""
    from bot.db.engine import DOWNLOAD_LOG_RETENTION_DAYS as engine_days

    migration = _load_migration()
    assert migration.DOWNLOAD_LOG_RETENTION_DAYS == engine_days


def test_migration_script_does_not_import_bot_engine_or_config():
    """Скрипт обязан работать без BOT_TOKEN/ADMIN_ID и без .env: и
    bot.db.engine, и bot.config на уровне модуля поднимают
    bot.config.settings (обязательные поля) и создают async-движок. Импорт
    любого из них здесь уронит обычный `python scripts/migrate_20260913.py`
    без переменных окружения — см. docstring EXTRA_INDEX_DDL в
    bot/db/models.py.

    Проверка через `ast`, а не через подстроку в тексте файла: имена модулей
    законно упоминаются в комментариях (объясняя, почему их НЕ импортируют),
    и наивный `"bot.db.engine" not in source` ловил бы и это тоже.
    """
    import ast

    tree = ast.parse((ROOT / "scripts" / "migrate_20260913.py").read_text())
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert "bot.db.engine" not in imported_modules
    assert "bot.config" not in imported_modules


def test_main_backs_up_database_before_touching_data(tmp_path):
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.execute("INSERT INTO users VALUES (1,'ivan','A',3,NULL,0,0,'2025-01-01','2025-01-01')")
    conn.commit()
    conn.close()

    migration = _load_migration()
    migration.main(["--database", str(db_path)])

    backups = sorted(tmp_path.glob("bot.db.bak-*"))
    assert len(backups) == 1
    # Бэкап реально читается и содержит то, что было ДО миграции.
    check = sqlite3.connect(backups[0])
    assert check.execute("SELECT username FROM users WHERE id = 1").fetchone() == ("ivan",)
    check.close()


def test_main_default_run_does_not_mutate_duplicates_or_purge_logs(tmp_path):
    """Решение 2 и 3: без явных флагов скрипт только показывает находки,
    ничего не удаляет и не переименовывает."""
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.executescript(
        """
        INSERT INTO users VALUES (1,'foo','A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO users VALUES (2,'Foo','B',3,NULL,0,0,'2025-01-01','2026-09-01');
        INSERT INTO download_log VALUES (1,1,'https://example.com/old','instagram','ok',1.0,'2020-01-01 00:00:00');
        """
    )
    conn.commit()
    conn.close()

    migration = _load_migration()
    rc = migration.main(["--database", str(db_path)])
    assert rc == 3  # незакрытые дубликаты -> уникальный индекс не создан

    check = sqlite3.connect(db_path)
    rows = dict(check.execute("SELECT id, username FROM users"))
    assert rows == {1: "foo", 2: "Foo"}  # дубликаты не тронуты
    assert check.execute("SELECT COUNT(*) FROM download_log").fetchone()[0] == 1  # лог не тронут
    names = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_users_username_nocase" not in names  # не создан из-за дублей
    assert "ix_download_log_created_at" in names  # обычные индексы безопасны всегда
    check.close()


def test_main_fix_duplicates_flag_applies_dedupe_and_creates_unique_index(tmp_path):
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.executescript(
        """
        INSERT INTO users VALUES (1,'foo','A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO users VALUES (2,'Foo','B',3,NULL,0,0,'2025-01-01','2026-09-01');
        """
    )
    conn.commit()
    conn.close()

    migration = _load_migration()
    rc = migration.main(["--database", str(db_path), "--fix-duplicates"])
    assert rc == 0

    check = sqlite3.connect(db_path)
    rows = dict(check.execute("SELECT id, username FROM users"))
    assert rows == {1: None, 2: "Foo"}
    names = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_users_username_nocase" in names
    check.close()


def test_main_purge_logs_flag_deletes_old_rows_only_with_flag(tmp_path):
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    old_date = "2020-01-01 00:00:00"
    recent_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.executescript(
        f"""
        INSERT INTO users VALUES (1,'ivan','A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO download_log VALUES (1,1,'https://example.com/old','instagram','ok',1.0,'{old_date}');
        INSERT INTO download_log VALUES (2,1,'https://example.com/new','instagram','ok',1.0,'{recent_date}');
        """
    )
    conn.commit()
    conn.close()

    migration = _load_migration()
    rc = migration.main(["--database", str(db_path), "--purge-logs"])
    assert rc == 0

    check = sqlite3.connect(db_path)
    remaining = [row[0] for row in check.execute("SELECT id FROM download_log ORDER BY id")]
    assert remaining == [2]
    check.close()
