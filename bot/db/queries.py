from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import DownloadLog, User


async def get_or_create_user(session: AsyncSession, tg_user) -> User:
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name,
            free_downloads_left=settings.FREE_DOWNLOADS,
        )
        session.add(user)
        await session.flush()
    else:
        # F10: пишем только при реальном изменении, иначе каждая сессия грязнит
        # строку и вызывает UPDATE (write-lock) даже когда ничего не поменялось.
        if user.username != tg_user.username:
            user.username = tg_user.username
        if user.full_name != tg_user.full_name:
            user.full_name = tg_user.full_name
    return user


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(
        select(User).where(User.username == username.lstrip("@"))
    )
    return result.scalar_one_or_none()


async def update_subscription(session: AsyncSession, user_id: int, days: int) -> None:
    user = await session.get(User, user_id)
    if user is None:
        return
    now = datetime.now(timezone.utc)
    existing = user.subscription_until
    if existing and existing.tzinfo is None:
        existing = existing.replace(tzinfo=timezone.utc)
    base = max(now, existing) if existing else now
    user.subscription_until = base + timedelta(days=days)
    await session.flush()


async def toggle_ban(session: AsyncSession, user_id: int) -> bool:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    user.is_banned = not user.is_banned
    await session.flush()
    return user.is_banned


# УСТАРЕЛО. Заменена на reserve_free_download: списание после доставки давало
# пользователю от трёх до семи скачиваний при одном оставшемся (C-1).
# Удаляется в Task 37, после того как пакет D перестанет её импортировать.
async def decrement_free_downloads(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(User)
        .where(User.id == user_id, User.free_downloads_left > 0)
        .values(free_downloads_left=User.free_downloads_left - 1)
    )
    await session.flush()


async def reserve_free_download(session: AsyncSession, user_id: int) -> bool:
    """Атомарно занимает одну бесплатную единицу. `True` — заняли.

    Условие `free_downloads_left > 0` проверяет СУБД в том же операторе,
    который декрементирует, поэтому окна между «проверили остаток» и
    «списали» больше нет: параллельные загрузки одного пользователя не
    могут занять одну и ту же единицу дважды.

    Успех определяется по `rowcount == 1`. Раньше `rowcount` не проверялся
    нигде в репозитории, и «не списалось» было неотличимо от «списалось».
    """
    result = await session.execute(
        update(User)
        .where(User.id == user_id, User.free_downloads_left > 0)
        .values(free_downloads_left=User.free_downloads_left - 1)
    )
    await session.flush()
    return result.rowcount == 1


async def refund_free_download(session: AsyncSession, user_id: int) -> None:
    """Возвращает ровно одну ранее зарезервированную единицу.

    Вызывающий обязан звать это не более одного раза на одно успешное
    резервирование: верхнего ограничителя здесь нет сознательно — админ
    может выдать пользователю больше единиц, чем FREE_DOWNLOADS, и упирать
    возврат в эту константу означало бы молча отнимать выданное.
    """
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(free_downloads_left=User.free_downloads_left + 1)
    )
    await session.flush()


async def increment_total_downloads(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(total_downloads=User.total_downloads + 1)
    )
    await session.flush()


async def log_download(
    session: AsyncSession,
    user_id: int,
    url: str,
    platform: str,
    status: str,
    file_size_mb: float | None = None,
) -> None:
    entry = DownloadLog(
        user_id=user_id,
        url=url,
        platform=platform,
        status=status,
        file_size_mb=file_size_mb,
    )
    session.add(entry)
    await session.flush()


async def get_stats(session: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)

    total_users = await session.scalar(select(func.count(User.id)))

    active_subscriptions = await session.scalar(
        select(func.count(User.id)).where(User.subscription_until > now)
    )

    downloads_24h = await session.scalar(
        select(func.count(DownloadLog.id)).where(
            DownloadLog.created_at >= now - timedelta(hours=24)
        )
    )

    downloads_7d = await session.scalar(
        select(func.count(DownloadLog.id)).where(
            DownloadLog.created_at >= now - timedelta(days=7)
        )
    )

    downloads_30d = await session.scalar(
        select(func.count(DownloadLog.id)).where(
            DownloadLog.created_at >= now - timedelta(days=30)
        )
    )

    return {
        "total_users": total_users or 0,
        "active_subscriptions": active_subscriptions or 0,
        "downloads_24h": downloads_24h or 0,
        "downloads_7d": downloads_7d or 0,
        "downloads_30d": downloads_30d or 0,
    }
