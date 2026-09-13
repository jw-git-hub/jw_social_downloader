from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255))
    free_downloads_left: Mapped[int] = mapped_column(default=3)
    subscription_until: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    is_banned: Mapped[bool] = mapped_column(default=False)
    total_downloads: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())


class DownloadLog(Base):
    __tablename__ = "download_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    url: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    file_size_mb: Mapped[Optional[float]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())


class SubscriptionGrant(Base):
    """Журнал изменений подписки: одна строка на каждое применённое изменение.

    `idempotency_key` уникален. Ключ генерирует UI при отрисовке карточки,
    поэтому повторный клик по той же кнопке — в том числе по карточке,
    оставшейся в истории чата админа с прошлого месяца, — даёт тот же ключ,
    и изменение не применяется второй раз.

    `days = NULL` означает снятие подписки, отрицательное — сокращение.

    `subscription_until_before`/`subscription_until_after` — состояние ДО
    и ПОСЛЕ операции. Строка самодостаточна для восстановления истории:
    не требует пересчёта по цепочке предыдущих строк, которая рвётся при
    любой правке `subscription_until` в обход `apply_subscription_change`
    (ревью фикс-раунда 1: журнал отвечал на «кому/когда/кем/сколько», но
    не на «с какого состояния» и «за что»). `reason` — произвольная
    привязка к платежу/причине, может быть `NULL`, если вызывающий её не
    передал.
    """

    __tablename__ = "subscription_grant"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    days: Mapped[Optional[int]] = mapped_column(nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    subscription_until_before: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    subscription_until_after: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now(), index=True)
