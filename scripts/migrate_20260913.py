#!/usr/bin/env python3
"""Разовая миграция под ревизию 2026-09-12 (фикс-раунд 1 от 2026-09-13).

Схема меняется ВСЕГДА, при каждом запуске, без каких-либо флагов: заводит
недостающие индексы (`EXTRA_INDEX_DDL`) на уже существующей боевой БД —
`create_all` их не делает, для существующей таблицы он пропускает её
целиком вместе с индексами, а `ALTER` не делает вовсе. Это безопасно всегда
(`CREATE INDEX IF NOT EXISTS`, чистое добавление, данные не трогает).

Флаг ниже управляет ТОЛЬКО изменением ДАННЫХ:

- `--purge-logs` — удалить строки `download_log` старше
  `DOWNLOAD_LOG_RETENTION_DAYS` (приватные пер-шаринговые токены в URL не
  должны храниться вечно). Без флага — только подсчёт, этот скрипт ничего
  не удаляет. ВАЖНО: это не убирает автоматический ретеншен в
  `bot/db/engine.py::init_db` — он безусловен и сработает сам на ближайшем
  рестарте бота, без бэкапа и без предупреждения, если строки уже старше
  порога, независимо от того, передан этот флаг здесь или нет.

Раньше в этой же ревизии была ещё и уникальность `users.username` без учёта
регистра плюс снятие дублирующихся ников (`--fix-duplicates`,
`dedupe_usernames`). **Убрано целиком по итогам ревью** (Critical):
ревьюер воспроизвёл на боевой схеме, что `get_or_create_user` роняет
`IntegrityError: UNIQUE constraint failed: users.username`, когда новый
пользователь занимает освободившийся ник или существующий переименовывается
в уже когда-то использованный, — необработанного `IntegrityError` в проекте
нет ни одного, и бот молча переставал отвечать конкретному человеку
навсегда. Причина глубже, чем недостающий `try/except`: ник в Telegram —
изменяемый и переиспользуемый внешний идентификатор, и несколько строк
`users`, когда-либо друг за другом носивших один и тот же ник, — легитимное
состояние данных, а не порча. `get_user_by_username`
(`bot/db/queries.py`) дубликаты уже переживает сам — берёт строку с самым
свежим `updated_at`; индекс по выражению `lower(username)`
(`ix_users_username_lower` в `EXTRA_INDEX_DDL`) даёт этому поиску ту же
скорость, ради которой всё затевалось, не вводя новый отказ. Подробности —
`task-21-report.md`.

Перед ЛЮБЫМ изменением данных скрипт сам делает бэкап файла БД (через
`sqlite3.Connection.backup` — корректный снимок средствами самой СУБД,
безопасно и при активном WAL) и проверяет, что бэкап реально читается.
ВАЖНО: по умолчанию бэкап ложится РЯДОМ с оригиналом — если оригинал внутри
docker-тома, бэкап окажется в ТОМ ЖЕ томе и не защитит от потери самого
тома/диска (`docker volume rm`, `docker compose down -v`, смерть диска).
Передайте `--backup-dir` с каталогом, смонтированным отдельно (например, с
хоста), для реальной защиты — скрипт прямо скажет в выводе, куда бэкап лёг.

Останавливать бота не требуется: SQLite в режиме WAL, `busy_timeout` — 30
секунд и на рабочем соединении, и на бэкапе, конкурентные операции бота
ждут снятия блокировки вместо мгновенной ошибки.

    docker build --target test -t jw_downloader:test .

    # Сначала — на КОПИИ боевой БД, не на самом томе.
    docker run --rm -v /path/to/copy:/work -v /path/to/host/backups:/backup \\
        jw_downloader:test python scripts/migrate_20260913.py \\
        --database /work/bot.db --backup-dir /backup

    # После проверки копии и только по решению владельца — на боевом томе,
    # с бэкапом на ОТДЕЛЬНО смонтированном каталоге хоста:
    docker run --rm -v jw_downloader_bot_data:/app/data -v /host/backups:/backup \\
        jw_downloader:test python scripts/migrate_20260913.py \\
        --database /app/data/bot.db --backup-dir /backup --purge-logs
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db.models import EXTRA_INDEX_DDL  # noqa: E402

# Совпадает с bot.db.engine.DOWNLOAD_LOG_RETENTION_DAYS. НЕ импортируется
# оттуда: bot.db.engine на уровне модуля поднимает bot.config.settings
# (обязательные BOT_TOKEN/ADMIN_ID) и создаёт async-движок — а этому скрипту,
# работающему с БД напрямую через sqlite3, ни то ни другое не нужно и не
# должно требовать .env/токен бота при обычном разовом запуске (см. тот же
# принцип в комментарии у EXTRA_INDEX_DDL в bot/db/models.py). Расхождение
# значений ловит tests/test_schema_migration.py::test_retention_days_constant_matches_engine.
DOWNLOAD_LOG_RETENTION_DAYS = 180


def backup_database(db_path: Path, backup_dir: Path | None = None) -> Path:
    """Бэкап файла БД, безопасный для WAL. Обязательный шаг САМОГО скрипта.

    По умолчанию (`backup_dir=None`) ложится рядом с оригиналом — если
    оригинал внутри docker-тома, бэкап окажется в ТОМ ЖЕ томе и не защитит
    от потери самого тома/диска. `backup_dir` — каталог, смонтированный
    отдельно (например, с хоста), даёт реальную защиту; вызывающий обязан
    предупредить об этом в выводе, если `backup_dir` не передан (это делает
    `main()`, не эта функция — она только копирует).

    `sqlite3.Connection.backup` — Online Backup API SQLite: читает
    согласованный снимок через саму СУБД, а не копированием файлов
    (`bot.db`/`bot.db-wal`/`bot.db-shm` по отдельности не атомарны при живом
    писателе). `timeout=30` на обоих соединениях — как на рабочем
    соединении ниже, чтобы бэкап не спотыкался о блокировку на дефолтных 5
    секундах sqlite3, когда рабочее соединение ждёт все 30.

    После копирования бэкап открывается и проверяется реальным запросом —
    «файл скопировался» не значит «из него можно восстановиться».
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    directory = backup_dir if backup_dir is not None else db_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    backup_path = directory / f"{db_path.name}.bak-{timestamp}"

    source = sqlite3.connect(db_path, timeout=30)
    try:
        dest = sqlite3.connect(backup_path, timeout=30)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    check = sqlite3.connect(backup_path, timeout=30)
    try:
        check.execute("SELECT COUNT(*) FROM users").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"бэкап {backup_path} создан, но не читается: {exc}") from exc
    finally:
        check.close()

    return backup_path


def _retention_cutoff() -> str:
    """Отсечка в формате, которым SQLite (`func.now()` = `CURRENT_TIMESTAMP`)
    пишет `created_at`: `'YYYY-MM-DD HH:MM:SS'`, без долей секунды, UTC.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=DOWNLOAD_LOG_RETENTION_DAYS)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Миграция схемы под ревизию 2026-09-12. Индексы заводятся ВСЕГДА, "
            "при каждом запуске, без флагов — это безопасно (IF NOT EXISTS, "
            "данные не трогает). Флаг ниже управляет только изменением ДАННЫХ."
        ),
    )
    parser.add_argument("--database", required=True, type=Path, help="путь к bot.db")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help=(
            "куда положить бэкап перед миграцией. По умолчанию — рядом с "
            "--database (тот же каталог/docker-том, что и оригинал: НЕ "
            "защищает от потери самого тома/диска). Укажите каталог, "
            "смонтированный отдельно (например, с хоста), для реальной защиты."
        ),
    )
    parser.add_argument(
        "--purge-logs",
        action="store_true",
        help=(
            f"удалить строки download_log старше {DOWNLOAD_LOG_RETENTION_DAYS} дней. "
            "Без флага — только подсчёт, этот скрипт ничего не удаляет (но "
            "автоматический ретеншен бота при старте всё равно удалит их сам, "
            "см. предупреждение в выводе)."
        ),
    )
    args = parser.parse_args(argv)

    if not args.database.is_file():
        print(f"файла нет: {args.database}")
        return 1

    try:
        backup_path = backup_database(args.database, args.backup_dir)
        print(f"бэкап создан и проверен: {backup_path}")
        if args.backup_dir is None:
            print(
                "ВНИМАНИЕ: бэкап лёг РЯДОМ с оригиналом, в том же каталоге/томе "
                "— не защищает от потери самого тома/диска (docker volume rm, "
                "docker compose down -v, смерть диска). Передайте --backup-dir "
                "с отдельно смонтированным каталогом для реальной защиты."
            )

        conn = sqlite3.connect(args.database, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")

            for statement in EXTRA_INDEX_DDL:
                conn.execute(statement)
            conn.commit()
            print("индексы созданы (это всегда, схема меняется при каждом запуске)")

            cutoff = _retention_cutoff()
            old_count = conn.execute(
                "SELECT COUNT(*) FROM download_log WHERE created_at < ?", (cutoff,)
            ).fetchone()[0]
            print(f"строк download_log старше {DOWNLOAD_LOG_RETENTION_DAYS} дней: {old_count}")

            if old_count and args.purge_logs:
                cursor = conn.execute("DELETE FROM download_log WHERE created_at < ?", (cutoff,))
                conn.commit()
                print(f"удалено строк download_log: {cursor.rowcount}")
            elif old_count:
                print(
                    "строки НЕ удалены этим скриптом (нет флага --purge-logs) — "
                    "это только подсчёт. Это НЕ значит, что они в безопасности: "
                    "ретеншен в bot/db/engine.py::init_db безусловен и удалит их "
                    "сам на ближайшем рестарте бота — без бэкапа и без "
                    "предупреждения, раз они уже старше порога."
                )
        finally:
            conn.close()
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        print(f"миграция прервана ошибкой: {exc}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
