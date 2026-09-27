"""Уведомление о ссылках, прерванных рестартом бота.

Вызывается один раз при старте, до поллинга (`bot/__main__.py`), — иначе
бесплатный пользователь терял бы бронь молча, а статус-сообщение замерзало
бы навсегда до следующего деплоя (Д11 плана
`.superpowers/sdd/2026-09-27-queue/plan.md`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from aiogram.types import LinkPreviewOptions
from loguru import logger

from bot.db.pending_downloads import InterruptedDownload, release_interrupted
from bot.services.queue_texts import interrupted_text

# О строках старше суток не пишем: пользователь давно прислал ссылку заново
# или забыл про неё. Бронь по ним всё равно возвращается — молчим только
# про уведомление, не про саму уборку.
INTERRUPTED_NOTICE_MAX_AGE = timedelta(hours=24)


def _group_fresh_by_chat(
    interrupted: list[InterruptedDownload], now: datetime
) -> dict[int, list[InterruptedDownload]]:
    """Только строки не старше `INTERRUPTED_NOTICE_MAX_AGE`, по порядку появления."""
    by_chat: dict[int, list[InterruptedDownload]] = defaultdict(list)
    for item in interrupted:
        if now - item.created_at <= INTERRUPTED_NOTICE_MAX_AGE:
            by_chat[item.chat_id].append(item)
    return by_chat


async def _send_restart_notice(bot, chat_id: int, items: list[InterruptedDownload]) -> None:
    """Одно сообщение в чат. Сбой — не повод останавливать рассылку остальным."""
    text = interrupted_text([item.url for item in items], any(item.refunded for item in items))
    try:
        await bot.send_message(
            chat_id, text, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    except Exception as exc:
        logger.info(
            "Не удалось отправить уведомление о рестарте | chat_id={} error={}", chat_id, exc
        )


async def notify_interrupted_downloads(bot, session_factory, now: datetime | None = None) -> None:
    """Разбирает журнал `pending_download`, возвращает брони, рассылает уведомления.

    Любая ошибка любого шага — best-effort: старт бота она срывать не должна.
    """
    now = now or datetime.now(timezone.utc)

    try:
        async with session_factory() as session, session.begin():
            interrupted = await release_interrupted(session)
    except Exception:
        logger.exception("Уборка прерванных загрузок при старте провалилась")
        return

    by_chat = _group_fresh_by_chat(interrupted, now)
    for chat_id, items in by_chat.items():
        await _send_restart_notice(bot, chat_id, items)

    logger.info(
        "Прерванные загрузки после рестарта | links={} chats={}", len(interrupted), len(by_chat)
    )
