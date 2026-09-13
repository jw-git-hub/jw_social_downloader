#!/usr/bin/env python3
"""Вычистить секреты из уже написанных лог-файлов.

Боевой лог лежит в именованном томе, поэтому запускать внутри контейнера:

    docker build --target test -t jw_downloader:test .
    docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test \\
        python scripts/scrub_logs.py /app/data/bot.log

Бота останавливать не нужно: `scrub_log_file` правит файл на месте (тот же
inode), а не через временный файл с последующей подменой — открытый
файловый дескриптор живого писателя (loguru, mode="a"/O_APPEND) не
осиротеет и продолжит писать по тому же пути. ОГОВОРКА: это гарантия
только для записей ПОСЛЕ завершения скрипта — строка, которую бот
дописывает МЕЖДУ чтением файла и его усечением внутри одного вызова
`scrub_log_file`, молча теряется (окно — доли секунды на типичный файл).
Это всё равно несравнимо лучше прежнего поведения (там терялось ВСЁ до
перезапуска бота, а секрет утекал в открепленный inode). Полный разбор
прежнего бага с подменой inode, обоснование выбора и разбор окна гонки —
в докстринге `bot.utils.log_guard.scrub_log_file`.

Скрипт правит только файловый лог. Лог контейнера (json-file) он не трогает —
тот усекается пересозданием контейнера, что в этот план не входит.

Проверка результата — ОБЯЗАТЕЛЬНО тем же регэкспом, что использует
маскировщик (`_BOT_SECRET_RE`), а не отдельным вручную переписанным
шаблоном: в первой версии раннбука шаблон верификации был на основе
границы слова, и ревью показало, что граница слова не встаёт между буквой
и цифрой — между «t» в «bot» и первой цифрой id, доминирующая форма
секрета (`/bot<цифры>:...`) им не ловится, и «осталось совпадений: 0»
ничего не доказывает:

    docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test python -c "from bot.utils.log_guard import _BOT_SECRET_RE as R; t = open('/app/data/bot.log', encoding='utf-8', errors='replace').read(); print('осталось совпадений:', len(R.findall(t)))"

Ожидается: `осталось совпадений: 0`.
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
