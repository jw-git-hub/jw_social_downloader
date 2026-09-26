"""Бесплатный лимит: FREE_DOWNLOADS_PER_DAY скачиваний за скользящие FREE_WINDOW.

Каждое бесплатное скачивание — строка `free_download`. Бронь делается ДО
загрузки, неудачная загрузка удаляет свою строку — так списывается только
реально полученное (C-1: квоту резервируем, а не проверяем).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, delete, func, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import FreeDownload
from bot.db.queries import _as_utc

FREE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class FreeQuota:
    """`left` — сколько бесплатных осталось в окне; `next_at` (UTC) — когда
    откроется следующее, только если `left == 0`."""

    left: int
    next_at: datetime | None


async def reserve_free_download(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> int | None:
    """Занимает бесплатное скачивание. Возвращает id брони или None (лимит).

    Сначала удаляет брони пользователя, вышедшие из окна, потом ОДНИМ
    оператором вставляет новую, только если в окне меньше лимита. Первый же
    оператор записи берёт write-lock SQLite, и подсчёт идёт под ним — две
    параллельные загрузки не займут последний слот дважды (см. комментарий
    в bot/db/engine.py). Подписку и бан НЕ проверяет — это делает вызывающий.
    """
    now = now or datetime.now(timezone.utc)
    await session.execute(
        delete(FreeDownload).where(
            FreeDownload.user_id == user_id, FreeDownload.reserved_at <= now - FREE_WINDOW
        )
    )
    used = (
        select(func.count())
        .select_from(FreeDownload)
        .where(FreeDownload.user_id == user_id)
        .scalar_subquery()
    )
    guarded_row = select(literal(user_id), literal(now, DateTime())).where(
        used < settings.FREE_DOWNLOADS_PER_DAY
    )
    result = await session.execute(
        insert(FreeDownload)
        .from_select(["user_id", "reserved_at"], guarded_row)
        .returning(FreeDownload.id)
    )
    return result.scalar_one_or_none()


async def refund_free_download(session: AsyncSession, reservation_id: int) -> None:
    """Возвращает бронь при неудачной загрузке. Повтор с тем же id — no-op,
    поэтому двойной возврат одной брони невозможен."""
    await session.execute(delete(FreeDownload).where(FreeDownload.id == reservation_id))


async def free_quota_status(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> FreeQuota:
    """Остаток в окне и, при нуле, когда откроется следующее скачивание."""
    now = now or datetime.now(timezone.utc)
    used_at = (
        await session.scalars(
            select(FreeDownload.reserved_at)
            .where(FreeDownload.user_id == user_id, FreeDownload.reserved_at > now - FREE_WINDOW)
            .order_by(FreeDownload.reserved_at)
        )
    ).all()
    limit = settings.FREE_DOWNLOADS_PER_DAY
    if len(used_at) < limit:
        return FreeQuota(left=limit - len(used_at), next_at=None)
    # Слот освободится, когда из окна выйдет бронь, после которой занятых станет меньше лимита.
    return FreeQuota(left=0, next_at=_as_utc(used_at[len(used_at) - limit]) + FREE_WINDOW)
