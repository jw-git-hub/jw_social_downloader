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


# DDL, который `create_all` не выполнит на УЖЕ существующей таблице: для неё
# он пропускает таблицу целиком вместе с индексами, а `ALTER` не делает вовсе.
# Живёт здесь, а не в engine.py, чтобы скрипт миграции мог импортировать эти
# строки, не поднимая настройки и не создавая движок.
#
# `ix_users_username_lower` — индекс по выражению `lower(username)`, ОТДЕЛЬНО
# от уникального NOCASE-индекса ниже. Не дублирование: `get_user_by_username`
# в queries.py ищет через `func.lower(User.username) == needle`, а
# `EXPLAIN QUERY PLAN` показывает, что `username COLLATE NOCASE`-индекс для
# ЭТОГО конкретного запроса не используется планировщиком (разные формы
# выражения — совпадение по коллации не совпадение по функции) — без этого
# индекса поиск по нику остаётся полным сканированием, даже после того как
# уникальный индекс ниже создан. Без `WHERE username IS NOT NULL`: с ним
# планировщик на параметризованном `lower(username) = ?` индекс не берёт
# (проверено `EXPLAIN QUERY PLAN` — с частичным индексом снова SCAN), в
# отличие от уникального индекса, где `WHERE` присутствует ради значения
# (разрешить сколько угодно NULL), а не ради использования планировщиком.
EXTRA_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_download_log_created_at ON download_log (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_download_log_user_id ON download_log (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_users_subscription_until ON users (subscription_until)",
    "CREATE INDEX IF NOT EXISTS ix_users_username_lower ON users (lower(username))",
)

# Уникальность ника — отдельно: на существующей базе она может не примениться
# из-за исторических дубликатов, и падать на старте из-за этого нельзя.
# Частичный индекс: NULL-ников может быть сколько угодно.
#
# `COLLATE NOCASE` на самой колонке (а не уникальный индекс по
# `lower(username)`, как `ix_users_username_lower` выше, но UNIQUE) — для
# УНИКАЛЬНОСТИ это ровно то же множество конфликтов, но коллация — общее
# свойство сравнения колонки, а не индекс под одну конкретную форму запроса:
# работает и в `ORDER BY`/`LIKE`/прямом сравнении без функции-обёртки, а не
# только там, где кто-то явно написал `lower(username)`. Ников в Telegram вне
# ASCII не бывает (буквы, цифры, `_`), поэтому ASCII-only `NOCASE` SQLite не
# теряет дубликаты, которые ловит питоновский `str.lower()` (Unicode-aware) в
# queries.py.
USERNAME_UNIQUE_DDL: str = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_nocase "
    "ON users (username COLLATE NOCASE) WHERE username IS NOT NULL"
)
