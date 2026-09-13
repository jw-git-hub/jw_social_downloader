import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bot.db.models import EXTRA_INDEX_DDL

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
    assert "ix_users_subscription_until" in names
    assert "ix_users_username_lower" in names


async def test_foreign_keys_pragma_is_on(db_session):
    from sqlalchemy import text

    value = await db_session.scalar(text("PRAGMA foreign_keys"))
    assert value == 1


# ─────────────────────────────────────────────────────────────────────────
# Фикс-раунд 1 (ревью, 2026-09-13): уникальность users.username снята
# целиком (Critical — get_or_create_user ронял необработанный IntegrityError
# на легитимном переиспользовании ника). Тесты на USERNAME_UNIQUE_DDL и
# dedupe_usernames удалены вместе с самим кодом — их предмет больше не
# существует. Подробности выбора — bot/db/models.py и task-21-report.md.
# Ниже — то, что осталось актуальным, плюс новые проверки по итогам ревью.
# ─────────────────────────────────────────────────────────────────────────


def test_username_lower_index_is_used_by_the_real_lookup_query(tmp_path):
    """ix_users_username_lower должен реально выбираться планировщиком под
    ТОТ ЖЕ запрос, что делает get_user_by_username (`func.lower(User.username)
    == needle`, с ORDER BY/LIMIT) — иначе "индекс создан" ничего не значит.
    Это теперь единственный механизм, дающий поиску по нику скорость: после
    фикс-раунда 1 уникального индекса больше нет.
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
    # быть избирательным (1/30 таблицы). Версия теста на ОДНОМ user_id для
    # всех 300 строк держала планировщик перед ВЕРНЫМ выбором SCAN (индекс
    # без пользы, когда предикат не отсеивает почти ничего) — такой тест
    # проверял не то, что заявлял.
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
    import pytest

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


async def test_db_session_fixture_has_the_extra_indexes(db_session):
    """Ревью фикс-раунда 1: тестовая схема (`db_session`) должна структурно
    совпадать с тем, что заводит EXTRA_INDEX_DDL в проде — раньше фикстура
    создавала только то, что есть в metadata моделей, и весь сьют тестировал
    ДРУГУЮ схему, чем прод (ни один новый индекс не был виден тестам)."""
    from sqlalchemy import text

    rows = (
        await db_session.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
    ).fetchall()
    names = {row[0] for row in rows}
    assert "ix_users_username_lower" in names
    assert "ix_download_log_created_at" in names
    assert "ix_download_log_user_id" in names
    assert "ix_users_subscription_until" in names


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


def test_main_backup_dir_flag_writes_backup_outside_db_directory(tmp_path, capsys):
    """Важно 3 (фикс-раунд 1): бэкап рядом с оригиналом не защищает от
    потери самого тома. --backup-dir кладёт бэкап в отдельный каталог,
    который скрипт создаёт сам, если его ещё нет (смонтированный с хоста
    каталог может не существовать заранее), и предупреждение про том в этом
    случае не показывается (оно только для дефолтного расположения)."""
    db_dir = tmp_path / "volume"
    db_dir.mkdir()
    db_path = db_dir / "bot.db"
    conn = _legacy_db(db_path)
    conn.execute("INSERT INTO users VALUES (1,'ivan','A',3,NULL,0,0,'2025-01-01','2025-01-01')")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "host-backups"  # намеренно ещё не существует
    migration = _load_migration()
    rc = migration.main(["--database", str(db_path), "--backup-dir", str(backup_dir)])
    assert rc == 0

    backups = sorted(backup_dir.glob("bot.db.bak-*"))
    assert len(backups) == 1
    assert sorted(db_dir.glob("bot.db.bak-*")) == []  # в каталоге БД бэкапа нет

    out = capsys.readouterr().out
    assert "ВНИМАНИЕ" not in out


def test_main_warns_when_backup_dir_not_given(tmp_path, capsys):
    """Без --backup-dir бэкап ложится в тот же том, что и оригинал — скрипт
    обязан сказать об этом прямым текстом, а не молчать (Важно 3)."""
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.execute("INSERT INTO users VALUES (1,'ivan','A',3,NULL,0,0,'2025-01-01','2025-01-01')")
    conn.commit()
    conn.close()

    migration = _load_migration()
    migration.main(["--database", str(db_path)])

    out = capsys.readouterr().out
    assert "ВНИМАНИЕ" in out
    assert "том" in out


def test_main_default_run_creates_schema_but_does_not_purge_logs(tmp_path):
    """Важно 4 (фикс-раунд 1): схема (индексы) меняется ВСЕГДА, без флагов —
    это безопасно (IF NOT EXISTS). Флаг --purge-logs управляет ТОЛЬКО
    данными: без него старые строки download_log не трогаются."""
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.executescript(
        """
        INSERT INTO users VALUES (1,'foo','A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO download_log VALUES (1,1,'https://example.com/old','instagram','ok',1.0,'2020-01-01 00:00:00');
        """
    )
    conn.commit()
    conn.close()

    migration = _load_migration()
    rc = migration.main(["--database", str(db_path)])
    assert rc == 0

    check = sqlite3.connect(db_path)
    names = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_download_log_created_at" in names
    assert "ix_users_username_lower" in names
    assert check.execute("SELECT COUNT(*) FROM download_log").fetchone()[0] == 1  # лог не тронут
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


def test_main_warns_engine_will_purge_anyway_without_flag(tmp_path, capsys):
    """Minor 7 (фикс-раунд 1): владелец может сознательно не дать
    --purge-logs, но автоматический ретеншен в init_db безусловен и удалит
    эти же строки сам на ближайшем рестарте бота — без бэкапа и без
    предупреждения. Скрипт обязан сказать об этом прямо, а не создавать
    ложное чувство «раз я не удалил — значит, в безопасности»."""
    db_path = tmp_path / "bot.db"
    conn = _legacy_db(db_path)
    conn.executescript(
        """
        INSERT INTO users VALUES (1,'ivan','A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO download_log VALUES (1,1,'https://example.com/old','instagram','ok',1.0,'2020-01-01 00:00:00');
        """
    )
    conn.commit()
    conn.close()

    migration = _load_migration()
    migration.main(["--database", str(db_path)])

    out = capsys.readouterr().out
    assert "init_db" in out
    assert "рестарте" in out


def test_main_reports_human_readable_error_for_invalid_database_file(tmp_path, capsys):
    """Minor (фикс-раунд 1): main() не должен ронять голый traceback на
    ошибках SQLite/ОС — человекочитаемое сообщение и отдельный код возврата
    (не 0, не путается с «дубликаты найдены», которого больше не существует)."""
    bad_path = tmp_path / "bot.db"
    bad_path.write_text("это не sqlite файл, а обычный текст" * 5)

    migration = _load_migration()
    rc = migration.main(["--database", str(bad_path)])

    assert rc == 2
    out = capsys.readouterr().out
    assert "миграция прервана ошибкой" in out
