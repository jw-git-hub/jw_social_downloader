from __future__ import annotations

import asyncio
import math
import os
import shutil
import time
from pathlib import Path

from loguru import logger

from bot.config import settings

DOWNLOAD_DIR = Path(settings.DOWNLOAD_ROOT)

# Каталог кук живёт ровно столько, сколько идёт загрузка. Берём двойной
# DOWNLOAD_TIMEOUT: всё, что старше, гарантированно осиротело — процесс,
# который его создал, уже не может быть жив. Общий max_age_minutes здесь не
# годится: по умолчанию он 10 минут против 15-минутного DOWNLOAD_TIMEOUT, и
# подметальщик срезал бы куки у идущей загрузки.
COOKIE_DIR_PREFIX = "jw_cookies_"
COOKIE_DIR_MAX_AGE_SEC = settings.DOWNLOAD_TIMEOUT * 2

# Общий порог подметальщика ОБЯЗАН быть строго больше DOWNLOAD_TIMEOUT,
# выраженного в минутах — иначе загрузка, идущая дольше порога, подметается
# у себя из-под ног (H-находка ревизии 2026-09-12: __main__.py звал
# periodic_cleanup() без аргументов, брались умолчания сигнатуры 5/10 минут,
# и CLEANUP_MAX_AGE_MIN=45 из настроек не использовался вообще). 45 > 15
# сейчас сходится, но это совпадение конфигурации, а не гарантия: подними
# владелец DOWNLOAD_TIMEOUT — и дефект вернётся молча. Поэтому эффективный
# порог всегда ПЕРЕСЧИТЫВАЕТСЯ как max(настройка, 2×таймаута) — та же схема
# защиты, что уже применена к COOKIE_DIR_MAX_AGE_SEC несколькими строками
# выше, один источник правды (DOWNLOAD_TIMEOUT).


def _effective_cleanup_max_age_min(configured_min: int, timeout_sec: int) -> int:
    """Формула инварианта, вынесенная отдельной функцией ради юнит-теста на
    произвольных значениях (а не только на текущих settings). math.ceil — на
    случай, если DOWNLOAD_TIMEOUT не кратен 60 секундам: без округления вверх
    «дважды таймаут в минутах» мог бы оказаться меньше самого таймаута из-за
    отбрасывания дробной части.
    """
    timeout_min = math.ceil(timeout_sec / 60)
    return max(configured_min, timeout_min * 2)


EFFECTIVE_CLEANUP_MAX_AGE_MIN = _effective_cleanup_max_age_min(
    settings.CLEANUP_MAX_AGE_MIN, settings.DOWNLOAD_TIMEOUT
)
if EFFECTIVE_CLEANUP_MAX_AGE_MIN != settings.CLEANUP_MAX_AGE_MIN:
    # Не молчим: если это когда-нибудь сработает, владелец должен увидеть
    # причину в логе при следующем перезапуске, а не гадать, почему уборка
    # стала реже.
    logger.warning(
        "CLEANUP_MAX_AGE_MIN={} мин слишком мал для DOWNLOAD_TIMEOUT={}с; "
        "используется пересчитанный порог {} мин",
        settings.CLEANUP_MAX_AGE_MIN,
        settings.DOWNLOAD_TIMEOUT,
        EFFECTIVE_CLEANUP_MAX_AGE_MIN,
    )


async def remove_file(path: str | Path) -> None:
    try:
        os.unlink(path)
        logger.info("File removed: {}", path)
    except FileNotFoundError:
        # Файл уже подмели или он и не доехал — это штатная ситуация, а не сбой.
        # Раньше сюда прилетало по двенадцать ERROR-строк на одну карусель.
        logger.debug("File already gone: {}", path)
    except Exception as exc:
        logger.warning("Failed to remove file {}: {}", path, exc)


def _landed_at(entry: Path) -> float:
    """Момент, когда файл реально появился у нас на диске.

    `st_mtime` доверять нельзя: gallery-dl по умолчанию выставляет его из
    заголовка `Last-Modified` CDN, поэтому свежескачанный файл из старого
    поста приземляется с датой месячной давности и подметается через секунды
    после загрузки. `st_ctime` — время последнего изменения inode; `utime()`,
    которым gallery-dl двигает mtime назад, его только обновляет на «сейчас»
    и сдвинуть в прошлое не может. Максимум из двух и есть настоящий возраст.
    """
    st = entry.stat()
    return max(st.st_mtime, st.st_ctime)


def sweep_once(max_age_minutes: int, now: float | None = None) -> int:
    """Один проход подметальщика. Возвращает число удалённых файлов.

    Вынесен из цикла ради тестируемости: параметр `now` позволяет посмотреть
    на каталог «из будущего», не трогая часы и не подделывая ctime.
    """
    if not DOWNLOAD_DIR.exists():
        return 0

    now = time.time() if now is None else now
    max_age_sec = max_age_minutes * 60
    removed = 0

    for entry in DOWNLOAD_DIR.iterdir():
        try:
            if entry.is_dir():
                # Осиротевшие каталоги _ephemeral_cookies (downloader.py):
                # переживают только жёсткое убийство процесса (docker kill,
                # OOM-kill, отключение питания), поэтому свой, более широкий
                # порог — см. COOKIE_DIR_MAX_AGE_SEC. Любой другой каталог
                # по-прежнему не трогаем.
                if not entry.name.startswith(COOKIE_DIR_PREFIX):
                    continue
                if (now - _landed_at(entry)) <= COOKIE_DIR_MAX_AGE_SEC:
                    continue
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
                continue
            if not entry.is_file():
                continue
            if (now - _landed_at(entry)) <= max_age_sec:
                continue
            os.unlink(entry)
            removed += 1
        except FileNotFoundError:
            # Гонка с хендлером, который удалил файл сам. Не сбой.
            continue
        except Exception as exc:
            logger.warning("Cleanup failed for {}: {}", entry, exc)

    return removed


async def periodic_cleanup(
    interval_minutes: int = 5, max_age_minutes: int = EFFECTIVE_CLEANUP_MAX_AGE_MIN
) -> None:
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            removed = sweep_once(max_age_minutes)
        except Exception as exc:
            # Цикл обязан пережить любой сбой прохода: раньше одно исключение
            # убивало фоновую задачу навсегда и молча.
            logger.exception("Periodic cleanup pass failed: {}", exc)
            continue
        if removed:
            logger.info("Periodic cleanup: removed {} file(s)", removed)
