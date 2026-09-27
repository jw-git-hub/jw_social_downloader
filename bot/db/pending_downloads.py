"""Журнал ссылок, принятых в очередь, но ещё не докачанных.

Пишется при постановке в очередь (`add_pending`) и удаляется по окончании
загрузки на любом исходе, кроме отмены задачи при остановке бота
(`bot/handlers/user.py::_run_job`). При старте бота `release_interrupted`
разом возвращает брони по всем накопившимся строкам и очищает журнал —
уведомление пользователей об этом собирает
`bot/services/interrupted_downloads.py` (Д11 плана
`.superpowers/sdd/2026-09-27-queue/plan.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import FreeDownload, PendingDownload
from bot.db.queries import _as_utc


@dataclass(frozen=True)
class InterruptedDownload:
    """Одна прерванная рестартом ссылка: куда писать и что сказать."""

    chat_id: int
    url: str
    refunded: bool
    created_at: datetime


async def add_pending(
    session: AsyncSession,
    *,
    user_id: int,
    chat_id: int,
    url: str,
    reservation_id: int | None,
    now: datetime | None = None,
) -> int:
    """Пишет строку журнала при постановке ссылки в очередь. Возвращает id."""
    row = PendingDownload(
        user_id=user_id,
        chat_id=chat_id,
        url=url,
        reservation_id=reservation_id,
        created_at=now or datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row.id


async def remove_pending(session: AsyncSession, pending_id: int) -> None:
    """Убирает строку по окончании загрузки. Повтор с тем же id — no-op."""
    await session.execute(delete(PendingDownload).where(PendingDownload.id == pending_id))


async def release_interrupted(session: AsyncSession) -> list[InterruptedDownload]:
    """Возвращает брони и очищает журнал целиком. Вызывать один раз при старте.

    Сверка `user_id` при возврате брони защищает от переиспользования rowid
    SQLite: без AUTOINCREMENT id удалённой строки `free_download` может
    достаться заново другому пользователю, и мы вернули бы чужую бронь.
    """
    rows = (await session.scalars(select(PendingDownload).order_by(PendingDownload.id))).all()

    for row in rows:
        if row.reservation_id is not None:
            await session.execute(
                delete(FreeDownload).where(
                    FreeDownload.id == row.reservation_id,
                    FreeDownload.user_id == row.user_id,
                )
            )

    ids = [row.id for row in rows]
    if ids:
        await session.execute(delete(PendingDownload).where(PendingDownload.id.in_(ids)))

    return [
        InterruptedDownload(
            chat_id=row.chat_id,
            url=row.url,
            refunded=row.reservation_id is not None,
            created_at=_as_utc(row.created_at),
        )
        for row in rows
    ]
