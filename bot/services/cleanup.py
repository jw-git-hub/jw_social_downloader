from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from loguru import logger

DOWNLOAD_DIR = Path("/tmp/jw_downloads")


async def remove_file(path: str | Path) -> None:
    try:
        os.unlink(path)
        logger.info("File removed: {}", path)
    except Exception as exc:
        logger.error("Failed to remove file {}: {}", path, exc)


async def periodic_cleanup(interval_minutes: int = 5, max_age_minutes: int = 10) -> None:
    while True:
        await asyncio.sleep(interval_minutes * 60)
        if not DOWNLOAD_DIR.exists():
            continue

        now = time.time()
        max_age_sec = max_age_minutes * 60
        removed = 0

        for file in DOWNLOAD_DIR.iterdir():
            if file.is_file() and (now - file.stat().st_mtime) > max_age_sec:
                try:
                    os.unlink(file)
                    removed += 1
                except Exception as exc:
                    logger.error("Cleanup failed for {}: {}", file, exc)

        if removed:
            logger.info("Periodic cleanup: removed {} file(s)", removed)
