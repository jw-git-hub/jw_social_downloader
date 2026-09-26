import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from bot.db.engine import assert_schema_migrated

ROOT = Path(__file__).resolve().parent.parent
SUB_UNTIL = "2026-10-20 10:00:00.000000"


def _load_migration():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "migrate_20260926", ROOT / "scripts" / "migrate_20260926.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_db(path: Path) -> None:
    """Боевая схема до миграции: со счётчиком и действующей подпиской."""
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
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
        INSERT INTO users VALUES (42, 'ann', 'Ann', 0, '{SUB_UNTIL}', 0, 7,
                                  '2026-01-01 00:00:00', '2026-01-01 00:00:00');
        """
    )
    conn.commit()
    conn.close()


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_migration_adds_tables_drops_counter_and_keeps_subscriptions(tmp_path):
    db = tmp_path / "bot.db"
    _legacy_db(db)

    code = _load_migration().main(["--database", str(db), "--backup-dir", str(tmp_path / "bk")])

    assert code == 0
    assert "free_downloads_left" not in _columns(db, "users")
    assert {"user_id", "reserved_at"} <= _columns(db, "free_download")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT subscription_until, total_downloads FROM users").fetchone() == (SUB_UNTIL, 7)
    conn.close()
    assert list((tmp_path / "bk").iterdir()), "бэкап обязан лечь в --backup-dir"


def test_migration_is_safe_to_rerun(tmp_path):
    db = tmp_path / "bot.db"
    _legacy_db(db)
    migration = _load_migration()
    migration.main(["--database", str(db), "--backup-dir", str(tmp_path / "bk")])

    assert migration.main(["--database", str(db), "--backup-dir", str(tmp_path / "bk")]) == 0
    assert "free_downloads_left" not in _columns(db, "users")


def test_migration_refuses_missing_file(tmp_path):
    assert _load_migration().main(["--database", str(tmp_path / "nope.db")]) == 1


def test_startup_guard_refuses_unmigrated_schema(tmp_path):
    db = tmp_path / "bot.db"
    _legacy_db(db)
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn, pytest.raises(RuntimeError, match="migrate_20260926"):
            assert_schema_migrated(conn)
    finally:
        engine.dispose()


def test_startup_guard_accepts_fresh_and_migrated_schema(tmp_path):
    db = tmp_path / "bot.db"
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            assert_schema_migrated(conn)  # пустая база — таблицы users ещё нет
        _legacy_db(db)
        _load_migration().main(["--database", str(db), "--backup-dir", str(tmp_path / "bk")])
        with engine.connect() as conn:
            assert_schema_migrated(conn)
    finally:
        engine.dispose()
