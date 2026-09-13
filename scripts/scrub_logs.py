#!/usr/bin/env python3
"""Вычистить секреты из уже написанных лог-файлов.

Боевой лог лежит в именованном томе, поэтому запускать внутри контейнера:

    docker build --target test -t jw_downloader:test .
    docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test \\
        python scripts/scrub_logs.py /app/data/bot.log

Скрипт правит только файловый лог. Лог контейнера (json-file) он не трогает —
тот усекается пересозданием контейнера, что в этот план не входит.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.utils.log_guard import scrub_log_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Вычистить секреты из лог-файлов.")
    parser.add_argument("paths", nargs="+", type=Path, help="пути к лог-файлам")
    args = parser.parse_args(argv)

    total = 0
    for path in args.paths:
        if not path.is_file():
            print(f"пропущен (не файл): {path}")
            continue
        changed = scrub_log_file(path)
        total += changed
        print(f"{path}: изменено строк — {changed}")
    print(f"итого изменено строк: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
