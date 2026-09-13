#!/usr/bin/env python3
"""Разовая миграция под ревизию 2026-09-12.

Делает три вещи над УЖЕ существующей боевой БД (`create_all` их не делает —
для существующей таблицы он пропускает её целиком вместе с индексами):

1. Заводит недостающие индексы (`EXTRA_INDEX_DDL`) и пытается создать
   уникальный индекс по `users.username` без учёта регистра
   (`USERNAME_UNIQUE_DDL`).
2. Обнаруживает дубликаты ников (без учёта регистра — Telegram-ники
   регистронезависимы) и, только по явному флагу `--fix-duplicates`, снимает
   их через `dedupe_usernames`. Без флага скрипт лишь СООБЩАЕТ о конфликтах
   и ничего не меняет: какой из дублей настоящий — решение владельца, а не
   эвристики скрипта, и падать посередине миграции из-за исторических
   данных недопустимо.
3. Считает, сколько строк `download_log` старше `DOWNLOAD_LOG_RETENTION_DAYS`
   (приватные пер-шаринговые токены в URL не должны храниться вечно), и,
   только по явному флагу `--purge-logs`, удаляет их. Без флага — только
   подсчёт.

Оба флага по отдельности: пометить дубликаты можно без удаления старых
записей журнала, и наоборот.

Перед ЛЮБЫМ изменением данных скрипт сам делает бэкап файла БД рядом с
оригиналом (через `sqlite3.Connection.backup` — корректно и для WAL, читает
согласованный снимок средствами самого SQLite) и проверяет, что бэкап
реально читается. Это обязательный шаг самого скрипта, а не пункт инструкции
поверх него, который легко забыть выполнить перед прогоном.

Останавливать бота не требуется: SQLite в режиме WAL, `busy_timeout` — 30
секунд, конкурентные операции бота ждут снятия блокировки вместо мгновенной
ошибки.

    docker build --target test -t jw_downloader:test .

    # Сначала — на КОПИИ боевой БД, не на самом томе.
    docker run --rm -v /path/to/copy:/work jw_downloader:test \\
        python scripts/migrate_20260913.py --database /work/bot.db

    # После проверки копии и только по решению владельца — на боевом томе:
    docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test \\
        python scripts/migrate_20260913.py --database /app/data/bot.db \\
        --fix-duplicates --purge-logs
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db.models import EXTRA_INDEX_DDL, USERNAME_UNIQUE_DDL  # noqa: E402

# Совпадает с bot.db.engine.DOWNLOAD_LOG_RETENTION_DAYS. НЕ импортируется
# оттуда: bot.db.engine на уровне модуля поднимает bot.config.settings
# (обязательные BOT_TOKEN/ADMIN_ID) и создаёт async-движок — а этому скрипту,
# работающему с БД напрямую через sqlite3, ни то ни другое не нужно и не
# должно требовать .env/токен бота при обычном разовом запуске (см. тот же
# принцип в комментарии у EXTRA_INDEX_DDL в bot/db/models.py). Расхождение
# значений ловит tests/test_schema_migration.py::test_retention_days_constant_matches_engine.
DOWNLOAD_LOG_RETENTION_DAYS = 180


def backup_database(db_path: Path) -> Path:
    """Бэкап файла БД рядом с оригиналом, безопасный для WAL.

    Обязательный шаг САМОГО скрипта (не инструкции поверх него): выполняется
    до любого изменения данных, при любых флагах и даже если в итоге ничего
    менять не пришлось. `sqlite3.Connection.backup` — Online Backup API
    SQLite: читает согласованный снимок через саму СУБД, а не копированием
    файлов (`bot.db`/`bot.db-wal`/`bot.db-shm` по отдельности не атомарны
    при живом писателе).

    После копирования бэкап открывается и проверяется реальным запросом —
    «файл скопировался» не значит «из него можно восстановиться».
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak-{timestamp}")

    source = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(backup_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    check = sqlite3.connect(backup_path)
    try:
        check.execute("SELECT COUNT(*) FROM users").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"бэкап {backup_path} создан, но не читается: {exc}") from exc
    finally:
        check.close()

    return backup_path


def _group_usernames_case_insensitively(
    conn: sqlite3.Connection,
) -> dict[str, list[tuple[int, str, str]]]:
    """Только КОНФЛИКТУЮЩИЕ группы (2+ пользователя на один ник без учёта
    регистра). Ничего не меняет — чистое обнаружение, чтобы `main()` мог
    сначала честно сообщить о находках и только потом (по флагу) действовать.
    """
    rows = conn.execute(
        "SELECT id, username, updated_at FROM users WHERE username IS NOT NULL AND username <> ''"
    ).fetchall()
    groups: dict[str, list[tuple[int, str, str]]] = {}
    for user_id, username, updated_at in rows:
        groups.setdefault(username.lower(), []).append((user_id, username, updated_at or ""))
    return {key: members for key, members in groups.items() if len(members) > 1}


def dedupe_usernames(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Обнуляет ник у всех строк группы, кроме самой свежей.

    Возвращает список `(user_id, снятый ник)` в порядке возрастания id.
    Мутирует БД (через `conn`, коммитит вызывающий) — вызывать только когда
    решение снять дубликаты уже принято (см. флаг `--fix-duplicates` в
    `main()`), не как часть обнаружения.
    """
    cleared: list[tuple[int, str]] = []
    for members in _group_usernames_case_insensitively(conn).values():
        # Позже обновлялся — тот и оставляет ник за собой.
        ordered = sorted(members, key=lambda m: (m[2], m[0]), reverse=True)
        for user_id, username, _ in ordered[1:]:
            conn.execute("UPDATE users SET username = NULL WHERE id = ?", (user_id,))
            cleared.append((user_id, username))

    cleared.sort()
    return cleared


def _retention_cutoff() -> str:
    """Отсечка в формате, которым SQLite (`func.now()` = `CURRENT_TIMESTAMP`)
    пишет `created_at`: `'YYYY-MM-DD HH:MM:SS'`, без долей секунды, UTC.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=DOWNLOAD_LOG_RETENTION_DAYS)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Миграция схемы под ревизию 2026-09-12.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--database", required=True, type=Path, help="путь к bot.db")
    parser.add_argument(
        "--fix-duplicates",
        action="store_true",
        help=(
            "снять дублирующиеся ники (dedupe_usernames) и создать уникальный индекс. "
            "Без флага — только отчёт о конфликтах, БД не меняется."
        ),
    )
    parser.add_argument(
        "--purge-logs",
        action="store_true",
        help=(
            f"удалить строки download_log старше {DOWNLOAD_LOG_RETENTION_DAYS} дней. "
            "Без флага — только подсчёт, ничего не удаляется."
        ),
    )
    args = parser.parse_args(argv)

    if not args.database.is_file():
        print(f"файла нет: {args.database}")
        return 1

    backup_path = backup_database(args.database)
    print(f"бэкап создан и проверен: {backup_path}")

    exit_code = 0
    conn = sqlite3.connect(args.database, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")

        # --- шаг 1: конфликтующие ники ---------------------------------
        conflicts = _group_usernames_case_insensitively(conn)
        if conflicts:
            print(f"конфликтующих ников (без учёта регистра): {len(conflicts)}")
            for key, members in sorted(conflicts.items()):
                print(f"  {key!r}: {members}")
        else:
            print("конфликтующих ников не найдено")

        if conflicts and args.fix_duplicates:
            cleared = dedupe_usernames(conn)
            conn.commit()
            for user_id, username in cleared:
                print(f"снят дублирующийся ник: id={user_id} username={username}")
            print(f"снято дублирующихся ников: {len(cleared)}")
        elif conflicts:
            print(
                "дубликаты НЕ тронуты (нет флага --fix-duplicates) — "
                "решение, что с ними делать, за владельцем."
            )

        # --- шаг 2: обычные индексы (безопасны всегда, IF NOT EXISTS) --
        for statement in EXTRA_INDEX_DDL:
            conn.execute(statement)
        conn.commit()
        print("обычные индексы созданы")

        # --- шаг 3: уникальный индекс по username -----------------------
        if _group_usernames_case_insensitively(conn):
            print(
                "уникальный индекс по users.username НЕ создан — в базе остаются "
                "конфликтующие ники. Запустить скрипт повторно с --fix-duplicates "
                "после решения владельца, что делать с дублями."
            )
            exit_code = 3
        else:
            conn.execute(USERNAME_UNIQUE_DDL)
            conn.commit()
            print("уникальный индекс по users.username создан")

        # --- шаг 4: ретеншен download_log --------------------------------
        cutoff = _retention_cutoff()
        old_count = conn.execute(
            "SELECT COUNT(*) FROM download_log WHERE created_at < ?", (cutoff,)
        ).fetchone()[0]
        print(f"строк download_log старше {DOWNLOAD_LOG_RETENTION_DAYS} дней: {old_count}")

        if old_count and args.purge_logs:
            conn.execute("DELETE FROM download_log WHERE created_at < ?", (cutoff,))
            conn.commit()
            print(f"удалено строк download_log: {old_count}")
        elif old_count:
            print("строки НЕ удалены (нет флага --purge-logs) — это только подсчёт")
    finally:
        conn.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
