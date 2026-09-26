#!/usr/bin/env python3
"""Миграция под оплату звёздами и лимит «3 в сутки» (2026-09-26).

1. Бэкап базы (тот же проверенный способ, что в migrate_20260913.py).
2. Создаёт недостающие таблицы из моделей — `free_download` и `star_payment`
   с индексами. `create_all` не трогает существующие таблицы.
3. Удаляет из `users` пожизненный счётчик `free_downloads_left`.

Подписки, платежи и журнал загрузок не трогает. Повторный запуск безопасен.
Останавливать бота на время миграции нужно: новый код не стартует на старой
схеме, старый — на новой (см. откат ниже).

Откат на старый образ бота — одна команда, бэкап восстанавливать не нужно:
    ALTER TABLE users ADD COLUMN free_downloads_left INTEGER NOT NULL DEFAULT 3;

    docker run --rm -v jw_downloader_bot_data:/app/data -v /host/backups:/backup \\
        jw_downloader:test python scripts/migrate_20260926.py \\
        --database /app/data/bot.db --backup-dir /backup
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine  # noqa: E402

from bot.db.models import LEGACY_FREE_COUNTER_COLUMN, Base  # noqa: E402
from migrate_20260913 import backup_database  # noqa: E402


def create_missing_tables(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


def drop_legacy_counter(db_path: Path) -> bool:
    """True — колонка была и удалена; False — её уже нет."""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if LEGACY_FREE_COUNTER_COLUMN not in columns:
            return False
        conn.execute(f"ALTER TABLE users DROP COLUMN {LEGACY_FREE_COUNTER_COLUMN}")
        conn.commit()
        return True
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Миграция под оплату звёздами и лимит 3 в сутки.")
    parser.add_argument("--database", required=True, type=Path, help="путь к bot.db")
    parser.add_argument(
        "--backup-dir", type=Path, default=None,
        help="куда положить бэкап; по умолчанию — рядом с базой (не защищает от потери тома)",
    )
    args = parser.parse_args(argv)
    if not args.database.is_file():
        print(f"файла нет: {args.database}")
        return 1
    try:
        print(f"бэкап создан и проверен: {backup_database(args.database, args.backup_dir)}")
        create_missing_tables(args.database)
        print("таблицы free_download и star_payment на месте")
        dropped = drop_legacy_counter(args.database)
        print("счётчик free_downloads_left удалён" if dropped else "счётчика free_downloads_left уже нет")
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        print(f"миграция прервана ошибкой: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
