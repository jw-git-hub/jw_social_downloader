"""Проверка свободного места перед загрузкой.

Если USB-диск `/mnt/storage` отвалится, Docker создаст каталог загрузок на
eMMC (там свободно единицы гигабайт), и одна гигабайтная загрузка положит
весь хост вместе с базой. Модуль не импортирует `bot.config`: пороговое
значение передаёт вызывающий код, а тесты подменяют `disk_usage` напрямую.
"""

from __future__ import annotations

from pathlib import Path
from shutil import disk_usage

from loguru import logger

BYTES_PER_GB = 1024**3


def has_free_space(path: Path, min_free_gb: float) -> bool:
    """True, если на диске с `path` свободно не меньше `min_free_gb`.

    При `OSError` — fail-open (`True`): каталога может ещё не быть, его
    создаст `download_media`, а блокировать все загрузки из-за сбоя
    `statvfs` хуже, чем изредка пропустить проверку.
    """
    try:
        free_bytes = disk_usage(path).free
    except OSError as exc:
        logger.warning("Не удалось проверить свободное место | path={} error={}", path, exc)
        return True
    return free_bytes >= min_free_gb * BYTES_PER_GB
