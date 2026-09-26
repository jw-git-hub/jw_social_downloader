# Оплата звёздами и лимит «3 в сутки» — план реализации

> **Для исполнителей:** план исполняется задача за задачей (superpowers:subagent-driven-development).
> Шаги — чекбоксы `- [ ]`. Каждая задача заканчивается зелёным полным прогоном и коммитом.

**Цель:** подписка только за Telegram Stars с автопродлением и возвратом из админки; бесплатно —
3 скачивания за скользящие 24 часа.

**Архитектура:** две новые таблицы (`free_download` — журнал бесплатных скачиваний,
`star_payment` — списания звёзд); запросы в `bot/db/free_quota.py` и `bot/db/payments.py`;
хендлеры оплаты в `bot/handlers/payments.py`, команды `/terms` `/support` `/paysupport` в
`bot/handlers/info.py`, возвраты в `bot/handlers/admin_payments.py`. Подписка меняется только
через существующую `apply_subscription_change` (журнал + идемпотентность).

**Стек:** Python 3.12, aiogram 3.31.0, SQLAlchemy 2.0 async + aiosqlite, SQLite 3.46, pytest.

**Спека:** `docs/superpowers/specs/2026-09-26-stars-payments-design.md` — читать вместе с планом.

## Глобальные ограничения

- Тесты запускаются ТОЛЬКО так: `./scripts/test.sh [аргументы pytest]` (сборка образа `test` +
  прогон в контейнере). На хосте Python 3.10 с битым aiogram — `pytest` на хосте не запускать.
- `bot/services/*` (логику скачивания) не трогать.
- Коммит: сообщение заканчивается содержательной строкой. Никаких `Co-Authored-By`,
  `Claude-Session`, «Generated with». Коммитить только перечисленные в задаче файлы.
- Никогда `git add -f`; пути `.claude/` и `.superpowers/` в git не добавлять.
- Не выкладывать: никаких `docker compose up`, правок боевого `.env`, обращений к боевой базе.
- `parse_mode=HTML` глобальный: всё, что пришло от пользователя или из `.env`, — через `esc()`
  из `bot/utils/text.py`.
- Время — UTC. SQLite хранит naive-UTC; aware-datetime при записи сохраняется как UTC
  (проверено), при чтении приводить через `_as_utc` из `bot/db/queries.py`.
- Стиль — как в окружающем коде: русские докстринги/комментарии, объясняющие «почему»;
  функции ~до 20 строк; вложенность ≤ 3; никаких магических чисел/строк — именованные константы;
  мёртвый код удалять.
- Тексты для пользователя — ровно те, что в плане.
- Перед коммитом — полный прогон `./scripts/test.sh -q`, должен быть зелёным.

## На что смотреть ревьюеру (не покрыто ничьими тестами, кроме добавленных ниже)

1. Платёж от человека, который ни разу не нажимал /start (строки `users` нет) → пользователь
   создаётся, подписка выдаётся. Тест — Task 2 (`test_payment_creates_missing_user`).
2. Продление после возврата первого платежа → `subscription_charge_id` = свой номер, не падает.
   Тест — Task 2 (`test_renewal_after_refunded_first_falls_back_to_own_charge`).
3. Экран подписки, когда подписка истекла минуту назад → показана кнопка покупки. Тест — Task 3
   (`test_subscribe_screen_offers_purchase_after_expiry`).
4. Лимит уменьшили настройкой ниже числа уже занятых броней → остаток 0, «следующее через»
   считается от брони, чьё истечение реально освобождает слот. Тест — Task 1
   (`test_status_when_limit_lowered_below_used`).
5. Уведомление админу о платеже пользователя без ника и с `<` в имени → «без ника», имя
   экранировано. Тест — Task 3 (`test_admin_notice_escapes_name_and_handles_missing_username`).

---

### Task 1: Бесплатный лимит «3 за скользящие сутки»

**Files:**
- Modify: `bot/config.py` (поле `FREE_DOWNLOADS` → `FREE_DOWNLOADS_PER_DAY`)
- Modify: `bot/db/models.py` (удалить `User.free_downloads_left`, добавить `FreeDownload`,
  константу `LEGACY_FREE_COUNTER_COLUMN`)
- Create: `bot/db/free_quota.py`
- Modify: `bot/db/queries.py` (удалить `reserve_free_download`, `refund_free_download`,
  `decrement_free_downloads`; `get_or_create_user` без счётчика; поправить ссылку в докстринге
  `apply_subscription_change`)
- Modify: `bot/db/engine.py` (страховка `assert_schema_migrated` + комментарий C-1)
- Create: `scripts/migrate_20260926.py`
- Modify: `bot/utils/text.py` (`format_wait`)
- Modify: `bot/handlers/user.py` (бронь по id, тексты квоты, отказ на лимите)
- Modify: `bot/handlers/admin.py` (строка квоты в карточке, `_card_text`)
- Modify: `bot/keyboards/inline.py` (`get_help_kb` + кнопка «👑 Подписка»)
- Tests: create `tests/test_free_quota.py`, `tests/test_migration_20260926.py`;
  delete `tests/test_quota_queries.py`; rewrite `tests/test_user_quota.py`,
  `tests/test_user_texts.py`; modify `tests/conftest.py`, `tests/test_text.py`,
  `tests/test_admin_card.py`, `tests/test_user_handle_url.py`,
  `tests/test_silent_video_as_animation.py`, `tests/test_user_media.py`

**Interfaces:**
- Produces: `bot.db.free_quota`: `FREE_WINDOW: timedelta`, `FreeQuota(left: int, next_at: datetime | None)`,
  `reserve_free_download(session, user_id: int, now: datetime | None = None) -> int | None`,
  `refund_free_download(session, reservation_id: int) -> None`,
  `free_quota_status(session, user_id: int, now: datetime | None = None) -> FreeQuota`.
  `bot.db.models.FreeDownload`, `bot.db.models.LEGACY_FREE_COUNTER_COLUMN`.
  `bot.db.engine.assert_schema_migrated(sync_conn) -> None`.
  `bot.utils.text.format_wait(delta: timedelta) -> str`.
  `bot.handlers.user._active_subscription_until(user) -> datetime | None`,
  `_reserve_quota(session, tg_user) -> tuple[int, bool, bool, int | None]`,
  `_user_quota_state(tg_user) -> tuple[FreeQuota, bool]`,
  `_quota_line(quota, has_subscription)`, `_welcome_text(quota, has_subscription)`,
  `_help_text(quota, has_subscription)`, `_status_text(quota, sub_until, total_downloads)`,
  `_limit_reached_text(quota)`.
  `bot.handlers.admin._user_card_text(user, free_left: int) -> str`,
  `bot.handlers.admin._card_text(session, user) -> str`.
  Тестовый хелпер `tests/test_user_handle_url._seed_user(maker, *, free_left: int = 3, **overrides)`.

- [ ] **Step 1: Тесты журнала бесплатных скачиваний — создать `tests/test_free_quota.py`**

```python
"""Бесплатный лимит: FREE_DOWNLOADS_PER_DAY скачиваний за скользящие 24 часа."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import settings
from bot.db.free_quota import (
    FREE_WINDOW,
    free_quota_status,
    refund_free_download,
    reserve_free_download,
)
from bot.db.models import FreeDownload, User

UID = 1000000001
T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


async def _reserve_n(session, n, now=T0):
    return [await reserve_free_download(session, UID, now) for _ in range(n)]


async def _rows(session) -> int:
    return await session.scalar(
        select(func.count()).select_from(FreeDownload).where(FreeDownload.user_id == UID)
    )


async def test_three_per_day_then_refusal(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        ids = await _reserve_n(db_session, 3)
        fourth = await reserve_free_download(db_session, UID, T0)

    assert all(isinstance(i, int) for i in ids)
    assert len(set(ids)) == 3
    assert fourth is None


async def test_slot_reopens_exactly_after_24_hours(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        await _reserve_n(db_session, 3)
        just_before = await reserve_free_download(db_session, UID, T0 + FREE_WINDOW - timedelta(seconds=1))
        at_24h = await reserve_free_download(db_session, UID, T0 + FREE_WINDOW)

    assert just_before is None
    assert at_24h is not None


async def test_expired_rows_are_removed_so_table_does_not_grow(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        await _reserve_n(db_session, 3)
        await reserve_free_download(db_session, UID, T0 + timedelta(hours=25))
        assert await _rows(db_session) == 1


async def test_refund_returns_the_slot(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        ids = await _reserve_n(db_session, 3)
        await refund_free_download(db_session, ids[-1])
        assert (await free_quota_status(db_session, UID, T0)).left == 1
        assert await reserve_free_download(db_session, UID, T0) is not None


async def test_double_refund_returns_only_one_slot(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        ids = await _reserve_n(db_session, 2)
        await refund_free_download(db_session, ids[0])
        await refund_free_download(db_session, ids[0])
        assert (await free_quota_status(db_session, UID, T0)).left == settings.FREE_DOWNLOADS_PER_DAY - 1


async def test_status_reports_left_and_next_opening(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        await reserve_free_download(db_session, UID, T0)
        await reserve_free_download(db_session, UID, T0 + timedelta(hours=1))
        partial = await free_quota_status(db_session, UID, T0 + timedelta(hours=2))
        await reserve_free_download(db_session, UID, T0 + timedelta(hours=2))
        full = await free_quota_status(db_session, UID, T0 + timedelta(hours=3))

    assert partial.left == 1 and partial.next_at is None
    assert full.left == 0
    assert full.next_at == T0 + FREE_WINDOW  # освобождается самая старая бронь


async def test_status_when_limit_lowered_below_used(db_session, make_user, monkeypatch):
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        for hour in range(3):
            await reserve_free_download(db_session, UID, T0 + timedelta(hours=hour))
    monkeypatch.setattr(settings, "FREE_DOWNLOADS_PER_DAY", 1)

    status = await free_quota_status(db_session, UID, T0 + timedelta(hours=3))

    # Занято 3 при лимите 1: слот откроется, когда в окне станет 0 броней,
    # то есть когда истечёт самая свежая из трёх (T0 + 2 ч).
    assert status.left == 0
    assert status.next_at == T0 + timedelta(hours=2) + FREE_WINDOW


async def test_new_user_has_full_quota(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    status = await free_quota_status(db_session, UID, T0)
    assert status.left == settings.FREE_DOWNLOADS_PER_DAY
    assert status.next_at is None


async def test_concurrent_reservations_for_last_slot_yield_one_winner(
    db_session, make_user, tmp_path, sqlite_engine_factory
):
    """Два независимых соединения к одному файлу БД. Barrier — прямо перед бронью,
    иначе первая попытка успевает закоммититься и гонки нет; перед ним — чтение
    пользователя, как в хендлере (get_or_create_user)."""
    async with db_session.begin():
        db_session.add(make_user())
        await db_session.flush()
        await _reserve_n(db_session, settings.FREE_DOWNLOADS_PER_DAY - 1)

    engine_b = sqlite_engine_factory(tmp_path / "test.db")
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)
    barrier = asyncio.Barrier(2)

    async def attempt(session):
        async with session.begin():
            await session.get(User, UID)
            await barrier.wait()
            return await reserve_free_download(session, UID, T0)

    try:
        async with maker_b() as session_b:
            results = await asyncio.gather(attempt(db_session), attempt(session_b))
    finally:
        await engine_b.dispose()

    assert sum(r is not None for r in results) == 1
    assert (await free_quota_status(db_session, UID, T0)).left == 0
```

- [ ] **Step 2: Прогнать — упадёт на импорте**

Run: `./scripts/test.sh tests/test_free_quota.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.db.free_quota'`.

- [ ] **Step 3: Настройка, модель, запросы**

`bot/config.py`: добавить `from pydantic import Field`; строку `FREE_DOWNLOADS: int = 3` заменить на

```python
    # Бесплатные скачивания за скользящие 24 часа (см. bot/db/free_quota.py).
    FREE_DOWNLOADS_PER_DAY: int = Field(default=3, ge=1)
```

`bot/db/models.py`: в импорт sqlalchemy добавить `Index`; из `User` удалить строку
`free_downloads_left: Mapped[int] = mapped_column(default=3)`; после класса `DownloadLog` добавить:

```python
class FreeDownload(Base):
    """Одно занятое бесплатное скачивание.

    Строка живёт, пока не выйдет из окна `FREE_WINDOW` (её удаляет следующая
    бронь того же пользователя — в таблице у него не больше лимита строк) или
    пока загрузка не провалилась (тогда её удаляют сразу: неудачное скачивание
    не списывается). `reserved_at` пишет код, а не `func.now()`, — тесты
    подставляют «сейчас» и проверяют окно в 24 часа.
    """

    __tablename__ = "free_download"
    __table_args__ = (Index("ix_free_download_user_reserved", "user_id", "reserved_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    reserved_at: Mapped[datetime]


# Пожизненный счётчик бесплатных скачиваний, заменённый журналом `free_download`.
# Удаляется из боевой базы скриптом scripts/migrate_20260926.py; bot/db/engine.py
# отказывается стартовать, пока колонка на месте.
LEGACY_FREE_COUNTER_COLUMN = "free_downloads_left"
```

Создать `bot/db/free_quota.py`:

```python
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
```

`bot/db/queries.py`:
- в `get_or_create_user` удалить строку `free_downloads_left=settings.FREE_DOWNLOADS,`;
  если `settings` после этого нигде в файле не используется — удалить `from bot.config import settings`;
- удалить функции `decrement_free_downloads`, `reserve_free_download`, `refund_free_download`
  целиком вместе с комментариями-шапками «УСТАРЕЛО…» над ними;
- в докстринге `apply_subscription_change` фразу «тот же приём, что у `reserve_free_download` в
  этом же файле» заменить на «тот же приём «один оператор + вердикт по результату», что у
  брони в `bot/db/free_quota.py`».

- [ ] **Step 4: Прогнать тесты журнала**

Run: `./scripts/test.sh tests/test_free_quota.py -q`
Expected: 9 passed. (Остальной набор пока красный — чинится в следующих шагах.)

- [ ] **Step 5: Тесты миграции и страховки — создать `tests/test_migration_20260926.py`**

```python
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from bot.db.engine import assert_schema_migrated

ROOT = Path(__file__).resolve().parent.parent
SUB_UNTIL = "2026-10-20 10:00:00.000000"


def _load_migration():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "migrate_20260926", ROOT / "scripts" / "migrate_20260926.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_db(path: Path) -> None:
    """Боевая схема до миграции: со счётчиком и действующей подпиской."""
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username VARCHAR(255),
            full_name VARCHAR(255) NOT NULL,
            free_downloads_left INTEGER NOT NULL DEFAULT 3,
            subscription_until DATETIME,
            is_banned BOOLEAN NOT NULL DEFAULT 0,
            total_downloads INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        INSERT INTO users VALUES (42, 'ann', 'Ann', 0, '{SUB_UNTIL}', 0, 7,
                                  '2026-01-01 00:00:00', '2026-01-01 00:00:00');
        """
    )
    conn.commit()
    conn.close()


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_migration_adds_tables_drops_counter_and_keeps_subscriptions(tmp_path):
    db = tmp_path / "bot.db"
    _legacy_db(db)

    code = _load_migration().main(["--database", str(db), "--backup-dir", str(tmp_path / "bk")])

    assert code == 0
    assert "free_downloads_left" not in _columns(db, "users")
    assert {"user_id", "reserved_at"} <= _columns(db, "free_download")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT subscription_until, total_downloads FROM users").fetchone() == (SUB_UNTIL, 7)
    conn.close()
    assert list((tmp_path / "bk").iterdir()), "бэкап обязан лечь в --backup-dir"


def test_migration_is_safe_to_rerun(tmp_path):
    db = tmp_path / "bot.db"
    _legacy_db(db)
    migration = _load_migration()
    migration.main(["--database", str(db), "--backup-dir", str(tmp_path / "bk")])

    assert migration.main(["--database", str(db), "--backup-dir", str(tmp_path / "bk")]) == 0
    assert "free_downloads_left" not in _columns(db, "users")


def test_migration_refuses_missing_file(tmp_path):
    assert _load_migration().main(["--database", str(tmp_path / "nope.db")]) == 1


def test_startup_guard_refuses_unmigrated_schema(tmp_path):
    db = tmp_path / "bot.db"
    _legacy_db(db)
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn, pytest.raises(RuntimeError, match="migrate_20260926"):
            assert_schema_migrated(conn)
    finally:
        engine.dispose()


def test_startup_guard_accepts_fresh_and_migrated_schema(tmp_path):
    db = tmp_path / "bot.db"
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            assert_schema_migrated(conn)  # пустая база — таблицы users ещё нет
        _legacy_db(db)
        _load_migration().main(["--database", str(db), "--backup-dir", str(tmp_path / "bk")])
        with engine.connect() as conn:
            assert_schema_migrated(conn)
    finally:
        engine.dispose()
```

Прогон: `./scripts/test.sh tests/test_migration_20260926.py -q` → FAIL (нет скрипта и `assert_schema_migrated`).

- [ ] **Step 6: Страховка в `bot/db/engine.py` и скрипт миграции**

`bot/db/engine.py`: импорт моделей дополнить `LEGACY_FREE_COUNTER_COLUMN`; перед `async def init_db`
добавить функцию и вызвать её в `init_db` ПЕРВОЙ строкой внутри `async with async_engine.begin() as conn:`
(`await conn.run_sync(assert_schema_migrated)` — до `create_all`):

```python
def assert_schema_migrated(sync_conn) -> None:
    """Отказ стартовать на базе до scripts/migrate_20260926.py.

    На старой базе колонка счётчика — NOT NULL без умолчания в самой базе:
    новый код её не заполняет, и каждый новый пользователь падал бы на
    INSERT. Лучше честный отказ при старте, чем бот, работающий наполовину.
    """
    columns = {row[1] for row in sync_conn.exec_driver_sql("PRAGMA table_info(users)")}
    if LEGACY_FREE_COUNTER_COLUMN in columns:
        raise RuntimeError("База не смигрирована: сначала запусти scripts/migrate_20260926.py")
```

В большом комментарии у создания движка (блок «ВАЖНО (C-1, …)») заменить упоминания
`bot/db/queries.py: reserve_free_download` / «UPDATE в reserve_free_download» на
`bot/db/free_quota.py: reserve_free_download` и «первый оператор записи (DELETE) в
reserve_free_download». Смысл комментария не менять.

Создать `scripts/migrate_20260926.py`:

```python
#!/usr/bin/env python3
"""Миграция под оплату звёздами и лимит «3 в сутки» (2026-09-26).

1. Бэкап базы (тот же проверенный способ, что в migrate_20260913.py).
2. Создаёт недостающие таблицы из моделей — `free_download` и `star_payment`
   с индексами. `create_all` не трогает существующие таблицы.
3. Удаляет из `users` пожизненный счётчик `free_downloads_left`.

Подписки, платежи и журнал загрузок не трогает. Повторный запуск безопасен.
Останавливать бота на время миграции нужно: новый код не стартует на старой
схеме, старый — на новой (см. откат ниже).

Откат на старый образ бота — одна команда, бэкап восстанавливать не нужно:
    ALTER TABLE users ADD COLUMN free_downloads_left INTEGER NOT NULL DEFAULT 3;

    docker run --rm -v jw_downloader_bot_data:/app/data -v /host/backups:/backup \\
        jw_downloader:test python scripts/migrate_20260926.py \\
        --database /app/data/bot.db --backup-dir /backup
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine  # noqa: E402

from bot.db.models import LEGACY_FREE_COUNTER_COLUMN, Base  # noqa: E402
from migrate_20260913 import backup_database  # noqa: E402


def create_missing_tables(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


def drop_legacy_counter(db_path: Path) -> bool:
    """True — колонка была и удалена; False — её уже нет."""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if LEGACY_FREE_COUNTER_COLUMN not in columns:
            return False
        conn.execute(f"ALTER TABLE users DROP COLUMN {LEGACY_FREE_COUNTER_COLUMN}")
        conn.commit()
        return True
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Миграция под оплату звёздами и лимит 3 в сутки.")
    parser.add_argument("--database", required=True, type=Path, help="путь к bot.db")
    parser.add_argument(
        "--backup-dir", type=Path, default=None,
        help="куда положить бэкап; по умолчанию — рядом с базой (не защищает от потери тома)",
    )
    args = parser.parse_args(argv)
    if not args.database.is_file():
        print(f"файла нет: {args.database}")
        return 1
    try:
        print(f"бэкап создан и проверен: {backup_database(args.database, args.backup_dir)}")
        create_missing_tables(args.database)
        print("таблицы free_download и star_payment на месте")
        dropped = drop_legacy_counter(args.database)
        print("счётчик free_downloads_left удалён" if dropped else "счётчика free_downloads_left уже нет")
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        print(f"миграция прервана ошибкой: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Прогон: `./scripts/test.sh tests/test_migration_20260926.py -q` → 5 passed.
(Проверку таблицы `star_payment` в тест миграции добавляет Task 2 — в этой задаче её ещё нет.)

- [ ] **Step 7: `format_wait` — тест и реализация**

Добавить в `tests/test_text.py`:

```python
from datetime import timedelta

from bot.utils.text import format_wait


def test_format_wait_rounds_up_to_minutes():
    assert format_wait(timedelta(0)) == "1 мин"
    assert format_wait(timedelta(seconds=59)) == "1 мин"
    assert format_wait(timedelta(seconds=61)) == "2 мин"
    assert format_wait(timedelta(minutes=12)) == "12 мин"


def test_format_wait_hours():
    assert format_wait(timedelta(hours=3)) == "3 ч"
    assert format_wait(timedelta(hours=5, minutes=12)) == "5 ч 12 мин"
    assert format_wait(timedelta(hours=24)) == "24 ч"
    assert format_wait(timedelta(hours=5, minutes=11, seconds=1)) == "5 ч 12 мин"
```

В этом же файле в `test_db_session_fixture_gives_a_working_schema` удалить строку
`assert found.free_downloads_left == 3`.

`bot/utils/text.py` — добавить `import math`, `from datetime import timedelta` и:

```python
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60


def format_wait(delta: timedelta) -> str:
    """Сколько ждать, по-человечески: «5 ч 12 мин», «3 ч», «12 мин».

    Округляем ВВЕРХ до минуты: «через 5 ч 11 мин», сказанное за 59 секунд до
    5 ч 12 мин, обмануло бы человека. Меньше минуты — «1 мин», а не «0 мин».
    """
    minutes = max(1, math.ceil(delta.total_seconds() / SECONDS_PER_MINUTE))
    hours, minutes = divmod(minutes, MINUTES_PER_HOUR)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"
```

- [ ] **Step 8: Хендлер пользователя — `bot/handlers/user.py`**

Импорты: из `bot.db.queries` убрать `refund_free_download`, `reserve_free_download`; добавить
`from bot.db.free_quota import FreeQuota, free_quota_status, refund_free_download, reserve_free_download`;
`from bot.utils.text import esc, format_wait`; в `from datetime import …` добавить `timedelta`.

Заменить `_quota_line`, `_welcome_text`, `_help_text`, `_status_text` на:

```python
def _active_subscription_until(user) -> datetime | None:
    """Конец подписки в UTC, если она ещё действует; иначе None.

    SQLite отдаёт naive-datetime в UTC — без приведения сравнение с aware
    `now` падает. Раньше этот кусок был скопирован в трёх хендлерах.
    """
    until = user.subscription_until
    if until is None:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until if until > datetime.now(timezone.utc) else None


def _wait_text(quota: FreeQuota) -> str:
    """Через сколько откроется следующее бесплатное скачивание."""
    if quota.next_at is None:
        return format_wait(timedelta(0))
    return format_wait(quota.next_at - datetime.now(timezone.utc))


def _free_left_text(quota: FreeQuota) -> str:
    return f"осталось <b>{quota.left} из {settings.FREE_DOWNLOADS_PER_DAY}</b> на сутки"


def _quota_line(quota: FreeQuota, has_subscription: bool) -> str:
    """Одна строка о правах пользователя для приветствия и помощи."""
    if has_subscription:
        return "👑 Подписка активна — скачивай без ограничений."
    if quota.left > 0:
        return f"🎁 Бесплатно: {_free_left_text(quota)}."
    return (
        "⏳ Бесплатные на сутки закончились. "
        f"Следующее — через <b>{_wait_text(quota)}</b>. С подпиской — без ограничений."
    )


def _welcome_text(quota: FreeQuota, has_subscription: bool) -> str:
    return (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Я — бот для скачивания видео из соцсетей.\n\n"
        "🌐 <b>Поддерживаемые платформы:</b>\n"
        "├ 📸 Instagram\n"
        "├ 🎵 TikTok\n"
        "├ 📘 Facebook\n"
        "├ 📌 Pinterest\n"
        "└ 📺 YouTube\n\n"
        f"{_quota_line(quota, has_subscription)}\n\n"
        "Выбери действие 👇"
    )


def _help_text(quota: FreeQuota, has_subscription: bool) -> str:
    return (
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми <b>«Скачать видео»</b>\n"
        "2️⃣ Отправь ссылку на видео\n"
        "3️⃣ Дождись файла — длинное видео в высоком качестве качается минутами\n\n"
        "🌐 <b>Платформы:</b> Instagram, TikTok, Facebook, Pinterest, YouTube\n\n"
        f"{_quota_line(quota, has_subscription)}"
    )


def _status_text(quota: FreeQuota, sub_until: datetime | None, total_downloads: int) -> str:
    sub_text = "✅ до " + sub_until.strftime("%d.%m.%Y") if sub_until else "❌ Не активна"
    free_line = f"🎟 Бесплатно: {_free_left_text(quota)}"
    if quota.left == 0:
        free_line += f"\n⏳ Следующее — через <b>{_wait_text(quota)}</b>"
    return (
        f"📊 <b>Твой профиль:</b>\n\n"
        f"{free_line}\n"
        f"👑 Подписка: <b>{sub_text}</b>\n"
        f"📥 Всего скачано: <b>{total_downloads}</b>"
    )


def _limit_reached_text(quota: FreeQuota) -> str:
    return (
        "🚫 Бесплатные скачивания на сутки закончились.\n"
        f"Следующее откроется через <b>{_wait_text(quota)}</b>.\n"
        "С подпиской — без ограничений 👇"
    )
```

Заменить `_reserve_quota`, `_user_quota_state`, `_refund_quota`:

```python
async def _reserve_quota(session, tg_user) -> tuple[int, bool, bool, int | None]:
    """Первый шаг загрузки: пользователь, бан, подписка и бронь бесплатного скачивания.

    Возвращает (user_id, is_banned, has_subscription, reservation_id). Бронь —
    в той же транзакции, что и чтение: иначе между чтением и списанием
    помещаются параллельные загрузки того же юзера. Забаненному и подписчику
    не бронируется (reservation_id = None): первому нельзя, второму не нужно.
    """
    user = await get_or_create_user(session, tg_user)
    has_subscription = _active_subscription_until(user) is not None
    if user.is_banned or has_subscription:
        return user.id, user.is_banned, has_subscription, None
    reservation_id = await reserve_free_download(session, user.id)
    return user.id, user.is_banned, has_subscription, reservation_id


async def _user_quota_state(tg_user) -> tuple[FreeQuota, bool]:
    """(бесплатный остаток, активна ли подписка) — одна короткая транзакция."""
    async with async_session() as session, session.begin():
        user = await get_or_create_user(session, tg_user)
        has_subscription = _active_subscription_until(user) is not None
        quota = await free_quota_status(session, user.id)
    return quota, has_subscription


async def _refund_quota(reservation_id: int) -> None:
    """Возврат брони отдельной короткой транзакцией.

    Падение возврата не должно ронять хендлер: пользователь уже увидел ошибку,
    а потерянное скачивание — меньшее зло, чем необработанное исключение.
    """
    try:
        async with async_session() as session, session.begin():
            await refund_free_download(session, reservation_id)
    except Exception as exc:
        logger.error("Не удалось вернуть бесплатное скачивание | reservation={} error={}", reservation_id, exc)
```

`_quota_action(reserved: bool, …)` — не менять; вызывать как `_quota_action(reservation_id is not None, …)`.

Хендлеры:
- `cmd_start`, `cb_main_menu`, `cb_help`: `free_left, has_subscription = await _user_quota_state(...)`
  → `quota, has_subscription = await _user_quota_state(...)`, передавать `quota` в текст.
- `cb_status` — тело целиком:

```python
    await callback.answer()
    async with async_session() as session, session.begin():
        user = await get_or_create_user(session, callback.from_user)
        sub_until = _active_subscription_until(user)
        total_downloads = user.total_downloads
        quota = await free_quota_status(session, user.id)
    await _safe_edit(
        callback,
        _status_text(quota, sub_until, total_downloads),
        reply_markup=get_status_kb(is_admin=_is_admin(callback.from_user.id)),
    )
```

- `_process_download`: распаковка `db_user_id, is_banned, has_subscription, reservation_id = await _reserve_quota(...)`;
  ветка отказа:

```python
    if not has_subscription and reservation_id is None:
        quota, _ = await _user_quota_state(message.from_user)
        await message.answer(
            _limit_reached_text(quota),
            reply_markup=get_paywall_kb(is_admin=_is_admin(message.from_user.id)),
        )
        return
```

  Во ВСЕХ четырёх местах возврата: `if _quota_action(reserved, …) == "refund": await _refund_quota(db_user_id)`
  → `if _quota_action(reservation_id is not None, …) == "refund": await _refund_quota(reservation_id)`.
  Переменная `reserved` в файле больше не встречается.

`bot/keyboards/inline.py`, `get_help_kb` — первой строкой добавить кнопку подписки:

```python
    rows = [
        [InlineKeyboardButton(text="📥 Скачать видео", callback_data="menu:download")],
        [InlineKeyboardButton(text="👑 Подписка", callback_data="menu:subscribe")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
```

- [ ] **Step 9: Карточка админа — `bot/handlers/admin.py`**

Импорт: `from bot.db.free_quota import free_quota_status`. `_user_card_text(user)` →
`_user_card_text(user, free_left: int)`, строку `f"🎟 Бесплатных: {user.free_downloads_left}\n"` заменить на
`f"🎟 Бесплатно за сутки: осталось {free_left} из {settings.FREE_DOWNLOADS_PER_DAY}\n"`. Добавить:

```python
async def _card_text(session, user) -> str:
    """Карточка с актуальным бесплатным остатком — его нет в строке users."""
    quota = await free_quota_status(session, user.id)
    return _user_card_text(user, quota.left)
```

В `admin_text_handler`, `cb_grant`, `cb_ban` текст карточки считать ВНУТРИ открытой сессии:
`text = await _card_text(session, user) if user else None` и дальше слать `text` вместо
`_user_card_text(user)`. Пример для `cb_grant`:

```python
    async with async_session() as session, session.begin():
        await update_subscription(session, user_id, days)
        user = await get_user_by_id(session, user_id)
        text = await _card_text(session, user) if user else None
    await callback.answer(f"✅ Подписка +{days} дней")
    if text:
        await _safe_edit(callback, text, reply_markup=get_user_card_kb(user_id))
```

(`admin_text_handler`: после поиска `text = await _card_text(session, user) if user else None` внутри
`async with`; проверка `if not user` остаётся как есть; отправка — `message.answer(text, …)`.)

- [ ] **Step 10: Перевести существующие тесты на брони**

1. `tests/conftest.py`, `make_user`: удалить `"free_downloads_left": 3,`.
2. Удалить `tests/test_quota_queries.py` (`git rm`) — его заменил `tests/test_free_quota.py`.
3. `tests/test_admin_card.py`: из `_fake_user` удалить `free_downloads_left=2,`; каждый вызов
   `_user_card_text(x)` (строки 29, 36, 41, 47, 53, 60) → `_user_card_text(x, 2)`; добавить тест
   `assert "осталось 2 из 3" in _user_card_text(_fake_user(), 2)`.
4. `tests/test_user_handle_url.py`: импорты дополнить
   `from datetime import datetime, timedelta, timezone`, `from bot.config import settings`,
   `from bot.db.free_quota import free_quota_status`, `from bot.db.models import FreeDownload`
   (рядом с `User`); `select` удалить, если больше не нужен. Хелперы заменить:

```python
async def _seed_user(maker, *, free_left: int = 3, **overrides) -> None:
    """Пользователь с остатком free_left: недостающее до лимита занято бронями час назад."""
    fields = dict(
        id=800000001,
        username="tester",
        full_name="Test User",
        subscription_until=None,
        is_banned=False,
        total_downloads=0,
    )
    fields.update(overrides)
    used_at = datetime.now(timezone.utc) - timedelta(hours=1)
    async with maker() as session, session.begin():
        session.add(User(**fields))
        await session.flush()
        for _ in range(settings.FREE_DOWNLOADS_PER_DAY - free_left):
            session.add(FreeDownload(user_id=fields["id"], reserved_at=used_at))


async def _free_downloads_left(maker, user_id: int) -> int:
    async with maker() as session:
        return (await free_quota_status(session, user_id)).left
```

   В `test_paywall_when_no_free_downloads_left` проверку `"лимит" in …lower()` заменить на
   `"закончились" in msg.answer_calls[0][0]` и `"через" in msg.answer_calls[0][0]`.
5. Во всех трёх файлах `tests/test_user_handle_url.py`, `tests/test_silent_video_as_animation.py`,
   `tests/test_user_media.py`: в вызовах `_seed_user(...)` аргумент `free_downloads_left=` → `free_left=`
   (`grep -n "free_downloads_left" tests/` после правки должен быть пуст).
6. `tests/test_user_quota.py` — переписать целиком:

```python
from types import SimpleNamespace

from bot.config import settings
from bot.db.free_quota import free_quota_status, refund_free_download
from bot.db.models import User
from bot.handlers.user import _quota_action, _reserve_quota


def _tg_user(uid: int = 555001, username: str = "quota_tester", full_name: str = "Quota Tester"):
    return SimpleNamespace(id=uid, username=username, full_name=full_name)


async def _left(session, uid: int) -> int:
    return (await free_quota_status(session, uid)).left


# ── политика возврата брони ──


def test_quota_action_keeps_when_nothing_was_reserved():
    assert _quota_action(False, download_ok=False, media_sent_count=0) == "keep"
    assert _quota_action(False, download_ok=True, media_sent_count=0) == "keep"


def test_quota_action_refunds_when_download_failed():
    assert _quota_action(True, download_ok=False, media_sent_count=0) == "refund"


def test_quota_action_refunds_when_nothing_was_delivered():
    assert _quota_action(True, download_ok=True, media_sent_count=0) == "refund"


def test_quota_action_keeps_when_at_least_one_file_delivered():
    assert _quota_action(True, download_ok=True, media_sent_count=1) == "keep"
    assert _quota_action(True, download_ok=True, media_sent_count=5) == "keep"


# ── бронь в одной транзакции с чтением ──


async def test_reserve_quota_takes_one_slot_from_new_user(db_session):
    async with db_session.begin():
        uid, banned, has_sub, reservation_id = await _reserve_quota(db_session, _tg_user())
    assert (banned, has_sub) == (False, False)
    assert isinstance(reservation_id, int)
    assert await _left(db_session, uid) == settings.FREE_DOWNLOADS_PER_DAY - 1


async def test_fourth_download_of_the_day_is_refused(db_session):
    tg = _tg_user()
    async with db_session.begin():
        results = [(await _reserve_quota(db_session, tg))[3] for _ in range(settings.FREE_DOWNLOADS_PER_DAY + 1)]
    assert all(r is not None for r in results[:-1])
    assert results[-1] is None


async def test_reserve_quota_does_not_touch_banned_user(db_session):
    tg = _tg_user()
    async with db_session.begin():
        db_session.add(User(id=tg.id, username=tg.username, full_name=tg.full_name, is_banned=True))
        await db_session.flush()
        _, banned, _, reservation_id = await _reserve_quota(db_session, tg)
    assert banned is True and reservation_id is None
    assert await _left(db_session, tg.id) == settings.FREE_DOWNLOADS_PER_DAY


async def test_reserve_quota_skips_subscriber(db_session):
    from datetime import datetime, timedelta, timezone

    tg = _tg_user()
    until = datetime.now(timezone.utc) + timedelta(days=5)
    async with db_session.begin():
        db_session.add(User(id=tg.id, username=tg.username, full_name=tg.full_name, subscription_until=until))
        await db_session.flush()
        _, _, has_sub, reservation_id = await _reserve_quota(db_session, tg)
    assert has_sub is True and reservation_id is None
    assert await _left(db_session, tg.id) == settings.FREE_DOWNLOADS_PER_DAY


async def test_refund_returns_exactly_the_reserved_slot(db_session):
    tg = _tg_user()
    async with db_session.begin():
        _, _, _, reservation_id = await _reserve_quota(db_session, tg)
        await refund_free_download(db_session, reservation_id)
    assert await _left(db_session, tg.id) == settings.FREE_DOWNLOADS_PER_DAY
```

7. `tests/test_user_texts.py` — переписать тесты текстов (тест `test_incoming_text_prefers_text_then_caption`
   оставить как есть):

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.config import settings
from bot.db.free_quota import FreeQuota
from bot.handlers.user import (
    _help_text,
    _incoming_text,
    _limit_reached_text,
    _quota_line,
    _status_text,
    _welcome_text,
)

LIMIT = settings.FREE_DOWNLOADS_PER_DAY


def _exhausted(hours=5, minutes=12) -> FreeQuota:
    return FreeQuota(left=0, next_at=datetime.now(timezone.utc) + timedelta(hours=hours, minutes=minutes))


def test_quota_line_reads_as_remainder_for_the_day():
    line = _quota_line(FreeQuota(left=3, next_at=None), has_subscription=False)
    assert f"осталось <b>3 из {LIMIT}</b> на сутки" in line
    assert "3/3" not in line


def test_quota_line_for_exhausted_user_says_when_next_opens():
    line = _quota_line(_exhausted(), has_subscription=False)
    assert "закончились" in line
    assert "через <b>5 ч 12 мин</b>" in line
    assert "подпиской" in line


def test_quota_line_for_subscriber_ignores_free_counter():
    line = _quota_line(_exhausted(), has_subscription=True)
    assert "Подписка активна" in line
    assert "бесплатн" not in line.lower()


def test_welcome_shows_actual_remainder():
    text = _welcome_text(FreeQuota(left=1, next_at=None), has_subscription=False)
    assert f"1 из {LIMIT}" in text


def test_welcome_and_help_do_not_promise_seconds():
    full = FreeQuota(left=3, next_at=None)
    for text in (
        _welcome_text(full, False),
        _welcome_text(full, True),
        _help_text(full, False),
        _help_text(full, True),
    ):
        assert "за секунды" not in text
        assert "несколько секунд" not in text


def test_status_text_uses_remainder_wording():
    text = _status_text(FreeQuota(left=2, next_at=None), None, 7)
    assert f"осталось <b>2 из {LIMIT}</b> на сутки" in text
    assert "❌ Не активна" in text
    assert "<b>7</b>" in text


def test_status_text_shows_wait_when_exhausted():
    text = _status_text(_exhausted(hours=3, minutes=0), None, 0)
    assert "через <b>3 ч</b>" in text


def test_status_text_renders_active_subscription_date():
    until = datetime.now(timezone.utc) + timedelta(days=3)
    text = _status_text(FreeQuota(left=0, next_at=None), until, 0)
    assert until.strftime("%d.%m.%Y") in text


def test_limit_reached_text_says_when_and_offers_subscription():
    text = _limit_reached_text(_exhausted())
    assert "закончились" in text
    assert "через <b>5 ч 12 мин</b>" in text
    assert "подпиской" in text
```

- [ ] **Step 11: Полный прогон**

Run: `./scripts/test.sh -q`
Expected: всё зелёное. `grep -rn "free_downloads_left\|FREE_DOWNLOADS\b" bot/ tests/` → совпадения только
в `bot/db/models.py` (константа `LEGACY_FREE_COUNTER_COLUMN`), `scripts/`-тесте миграции и докстрингах.

- [ ] **Step 12: Коммит**

```bash
git add bot/config.py bot/db/models.py bot/db/free_quota.py bot/db/queries.py bot/db/engine.py \
  bot/utils/text.py bot/handlers/user.py bot/handlers/admin.py bot/keyboards/inline.py \
  scripts/migrate_20260926.py tests/
git commit -m "feat(user): free downloads become 3 per rolling 24 hours"
```

---

### Task 2: Платежи звёздами — данные

**Files:**
- Modify: `bot/db/models.py` (класс `StarPayment`)
- Create: `bot/db/payments.py`
- Test: create `tests/test_star_payments.py`; modify `tests/test_migration_20260926.py`
  (вернуть проверку `star_payment`)

**Interfaces:**
- Consumes: `apply_subscription_change`, `GrantOutcome`, `_as_utc` из `bot/db/queries.py`.
- Produces: `bot.db.models.StarPayment`; `bot.db.payments`: `SUBSCRIPTION_DAYS = 30`,
  `SYSTEM_ACTOR_ID = 0`, `PaymentOutcome(duplicate: bool, is_first: bool, subscription_until: datetime | None)`,
  `apply_star_payment(session, *, user_id: int, payment) -> PaymentOutcome` (`payment` — aiogram
  `SuccessfulPayment` или объект с полями `telegram_payment_charge_id`, `total_amount`,
  `invoice_payload`, `is_recurring`, `is_first_recurring`, `subscription_expiration_date`),
  `list_user_payments(session, user_id: int, limit: int) -> list[StarPayment]`,
  `get_payment(session, payment_id: int) -> StarPayment | None`,
  `apply_refund(session, *, payment_id: int, admin_id: int, now: datetime | None = None) -> bool`.

- [ ] **Step 1: Тесты — создать `tests/test_star_payments.py`**

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

from bot.db.models import StarPayment, SubscriptionGrant, User
from bot.db.payments import SUBSCRIPTION_DAYS, apply_refund, apply_star_payment, list_user_payments
from bot.db.queries import _as_utc

UID = 1000000001


def _payment(charge="ch_first", *, first=True, amount=250):
    return SimpleNamespace(
        telegram_payment_charge_id=charge,
        total_amount=amount,
        invoice_payload="sub_30d",
        is_recurring=True,
        is_first_recurring=True if first else None,
        subscription_expiration_date=int(datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()),
    )


async def _pay(session, payment, user_id=UID):
    async with session.begin():
        return await apply_star_payment(session, user_id=user_id, payment=payment)


async def _until(session, user_id=UID):
    session.expire_all()
    return _as_utc((await session.get(User, user_id)).subscription_until)


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_first_payment_gives_30_days(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    before = datetime.now(timezone.utc)

    outcome = await _pay(db_session, _payment())

    assert outcome.duplicate is False and outcome.is_first is True
    until = await _until(db_session)
    assert before + timedelta(days=SUBSCRIPTION_DAYS) <= until <= datetime.now(timezone.utc) + timedelta(days=SUBSCRIPTION_DAYS)
    row = (await list_user_payments(db_session, UID, 10))[0]
    assert row.subscription_charge_id == "ch_first" and row.amount == 250 and row.is_first


async def test_same_payment_twice_extends_once(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    until_once = await _until(db_session)

    again = await _pay(db_session, _payment())

    assert again.duplicate is True
    assert await _until(db_session) == until_once
    assert await _count(db_session, StarPayment) == 1
    assert await _count(db_session, SubscriptionGrant) == 1


async def test_renewal_shifts_end_by_30_days(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    first_end = await _until(db_session)

    renewal = await _pay(db_session, _payment("ch_renewal", first=False))

    assert renewal.is_first is False
    assert await _until(db_session) == first_end + timedelta(days=SUBSCRIPTION_DAYS)
    rows = await list_user_payments(db_session, UID, 10)
    assert rows[0].telegram_payment_charge_id == "ch_renewal"
    assert rows[0].subscription_charge_id == "ch_first"  # отмена автопродления — по первому платежу


async def test_payment_stacks_on_manual_subscription(db_session, make_user):
    manual_end = datetime.now(timezone.utc) + timedelta(days=10)
    async with db_session.begin():
        db_session.add(make_user(subscription_until=manual_end))
    await _pay(db_session, _payment())
    assert await _until(db_session) == manual_end + timedelta(days=SUBSCRIPTION_DAYS)


async def test_refund_removes_subscription_and_marks_payment(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    payment_id = (await list_user_payments(db_session, UID, 10))[0].id

    async with db_session.begin():
        applied = await apply_refund(db_session, payment_id=payment_id, admin_id=1)

    assert applied is True
    assert await _until(db_session) is None
    db_session.expire_all()
    assert (await db_session.get(StarPayment, payment_id)).refunded_at is not None


async def test_second_refund_is_a_noop(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    payment_id = (await list_user_payments(db_session, UID, 10))[0].id
    async with db_session.begin():
        await apply_refund(db_session, payment_id=payment_id, admin_id=1)
    grants = await _count(db_session, SubscriptionGrant)

    async with db_session.begin():
        again = await apply_refund(db_session, payment_id=payment_id, admin_id=1)

    assert again is False
    assert await _count(db_session, SubscriptionGrant) == grants


async def test_refund_of_unknown_payment_is_false(db_session):
    async with db_session.begin():
        assert await apply_refund(db_session, payment_id=999, admin_id=1) is False


async def test_renewal_after_refunded_first_falls_back_to_own_charge(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    await _pay(db_session, _payment())
    first_id = (await list_user_payments(db_session, UID, 10))[0].id
    async with db_session.begin():
        await apply_refund(db_session, payment_id=first_id, admin_id=1)

    await _pay(db_session, _payment("ch_late_renewal", first=False))

    row = (await list_user_payments(db_session, UID, 10))[0]
    assert row.subscription_charge_id == "ch_late_renewal"


async def test_payment_creates_missing_user(db_session):
    """apply_star_payment требует существующего пользователя — хендлер зовёт
    get_or_create_user в той же транзакции. Проверяем связку."""
    from bot.db.queries import get_or_create_user

    tg = SimpleNamespace(id=UID, username=None, full_name="New Payer")
    async with db_session.begin():
        user = await get_or_create_user(db_session, tg)
        outcome = await apply_star_payment(db_session, user_id=user.id, payment=_payment())
    assert outcome.duplicate is False
    assert await _until(db_session) is not None
```

Прогон: `./scripts/test.sh tests/test_star_payments.py -q` → FAIL (нет `bot.db.payments`).

- [ ] **Step 2: Модель `StarPayment` — `bot/db/models.py`, после `FreeDownload`**

```python
class StarPayment(Base):
    """Одно списание звёзд: первый платёж подписки или её продление.

    `telegram_payment_charge_id` уникален — повторная доставка того же
    платежа не создаёт вторую строку. `subscription_charge_id` — номер
    ПЕРВОГО платежа подписки: Telegram отменяет автопродление только по нему.
    `refunded_at` пуст, пока платёж не возвращён.
    """

    __tablename__ = "star_payment"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    telegram_payment_charge_id: Mapped[str] = mapped_column(String(255), unique=True)
    subscription_charge_id: Mapped[str] = mapped_column(String(255))
    amount: Mapped[int]
    invoice_payload: Mapped[str] = mapped_column(String(128))
    is_first: Mapped[bool]
    subscription_expiration: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    refunded_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
```

- [ ] **Step 3: Запросы — создать `bot/db/payments.py`**

```python
"""Платежи звёздами: запись списаний, продление подписки, возврат.

Подписка меняется только через apply_subscription_change — у каждого
изменения есть строка журнала и ключ от повтора: `stars:<charge_id>` для
оплаты, `refund:<charge_id>` для возврата.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import StarPayment
from bot.db.queries import apply_subscription_change

SUBSCRIPTION_DAYS = 30
# admin_id в журнале subscription_grant для изменений, которые сделал сам бот.
SYSTEM_ACTOR_ID = 0


@dataclass(frozen=True)
class PaymentOutcome:
    """`duplicate` — этот платёж уже был применён; `subscription_until` (UTC) — конец подписки."""

    duplicate: bool
    is_first: bool
    subscription_until: datetime | None


def _is_first(payment) -> bool:
    return bool(payment.is_first_recurring) or not payment.is_recurring


def _expiration(payment) -> datetime | None:
    if not payment.subscription_expiration_date:
        return None
    return datetime.fromtimestamp(payment.subscription_expiration_date, timezone.utc)


async def _subscription_charge_id(session: AsyncSession, user_id: int, payment) -> str:
    """Номер первого платежа подписки. У продления это последний невозвращённый
    первый платёж пользователя; если его нет — свой номер."""
    if _is_first(payment):
        return payment.telegram_payment_charge_id
    first = await session.scalar(
        select(StarPayment.telegram_payment_charge_id)
        .where(
            StarPayment.user_id == user_id,
            StarPayment.is_first.is_(True),
            StarPayment.refunded_at.is_(None),
        )
        .order_by(StarPayment.id.desc())
        .limit(1)
    )
    return first or payment.telegram_payment_charge_id


async def apply_star_payment(session: AsyncSession, *, user_id: int, payment) -> PaymentOutcome:
    """Записывает списание и продлевает подписку на SUBSCRIPTION_DAYS.

    Пользователь обязан существовать (хендлер зовёт get_or_create_user в той
    же транзакции). Звать внутри транзакции, которую коммитит вызывающий.
    Повтор того же платежа ничего не меняет и возвращает duplicate=True.
    """
    is_first = _is_first(payment)
    await session.execute(
        sqlite_insert(StarPayment.__table__)
        .values(
            user_id=user_id,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            subscription_charge_id=await _subscription_charge_id(session, user_id, payment),
            amount=payment.total_amount,
            invoice_payload=payment.invoice_payload,
            is_first=is_first,
            subscription_expiration=_expiration(payment),
        )
        .on_conflict_do_nothing(index_elements=["telegram_payment_charge_id"])
    )
    grant = await apply_subscription_change(
        session,
        user_id=user_id,
        admin_id=SYSTEM_ACTOR_ID,
        days=SUBSCRIPTION_DAYS,
        idempotency_key=f"stars:{payment.telegram_payment_charge_id}",
        reason="stars_first" if is_first else "stars_renewal",
    )
    return PaymentOutcome(
        duplicate=grant.duplicate, is_first=is_first, subscription_until=grant.subscription_until
    )


async def list_user_payments(session: AsyncSession, user_id: int, limit: int) -> list[StarPayment]:
    """Последние платежи пользователя, новые сверху."""
    result = await session.scalars(
        select(StarPayment)
        .where(StarPayment.user_id == user_id)
        .order_by(StarPayment.id.desc())
        .limit(limit)
    )
    return list(result.all())


async def get_payment(session: AsyncSession, payment_id: int) -> StarPayment | None:
    return await session.get(StarPayment, payment_id)


async def apply_refund(
    session: AsyncSession, *, payment_id: int, admin_id: int, now: datetime | None = None
) -> bool:
    """Отмечает платёж возвращённым и снимает подписку. False — платежа нет
    или он уже был возвращён (повторный вызов ничего не меняет)."""
    payment = await session.get(StarPayment, payment_id)
    if payment is None:
        return False
    marked = await session.execute(
        update(StarPayment)
        .where(StarPayment.id == payment_id, StarPayment.refunded_at.is_(None))
        .values(refunded_at=now or datetime.now(timezone.utc))
    )
    if marked.rowcount == 0:
        return False
    await apply_subscription_change(
        session,
        user_id=payment.user_id,
        admin_id=admin_id,
        days=None,
        idempotency_key=f"refund:{payment.telegram_payment_charge_id}",
        reason="stars_refund",
    )
    return True
```

- [ ] **Step 4: Проверка `star_payment` в `tests/test_migration_20260926.py`**

В `test_migration_adds_tables_drops_counter_and_keeps_subscriptions` после проверки `free_download`
добавить строку `assert "telegram_payment_charge_id" in _columns(db, "star_payment")`.

- [ ] **Step 5: Прогон и коммит**

Run: `./scripts/test.sh -q` → всё зелёное (в `tests/test_star_payments.py` — 9 passed).

```bash
git add bot/db/models.py bot/db/payments.py tests/test_star_payments.py tests/test_migration_20260926.py
git commit -m "feat(db): record Telegram Stars payments and refunds"
```

---

### Task 3: Оплата для пользователя, команды, антифлуд, очередь простоя

**Files:**
- Modify: `bot/config.py` (удалить реквизиты и цены в валютах, добавить `SUBSCRIPTION_PRICE_STARS`,
  переписать примеры опечаток в докстрингах)
- Create: `bot/handlers/payments.py`, `bot/handlers/info.py`
- Modify: `bot/handlers/user.py` (экран подписки; удалить реквизиты и `_support_text`)
- Modify: `bot/keyboards/inline.py` (`get_paywall_kb`, `get_subscribe_kb`; удалить `get_payment_details_kb`)
- Modify: `bot/middlewares/throttle.py` (платёж мимо антифлуда)
- Modify: `bot/__main__.py` (разбор очереди простоя, команды, роутеры)
- Modify: `bot/handlers/__init__.py`
- Tests: create `tests/test_payments_flow.py`, `tests/test_info_commands.py`; rewrite
  `tests/test_polling_startup.py`; modify `tests/test_user_escaping.py`, `tests/test_config_validation.py`,
  `tests/test_throttle.py`

**Interfaces:**
- Consumes: `apply_star_payment`, `PaymentOutcome`, `SUBSCRIPTION_DAYS` (Task 2);
  `get_or_create_user`; `_active_subscription_until`, `_safe_edit`, `_is_admin` в `user.py` (Task 1).
- Produces: `bot.handlers.payments`: `router`, `STARS_CURRENCY = "XTR"`, `SUBSCRIPTION_PAYLOAD = "sub_30d"`,
  `SUBSCRIPTION_PERIOD_SECONDS = 2592000`, `CANCEL_HINT: str`, `PAYMENT_PENDING_TEXT: str`,
  `get_invoice_link(bot) -> str` (кэш модуля `_invoice_link`), `on_pre_checkout(query)`,
  `on_successful_payment(message)`, `notify_admin(bot, text) -> None`.
  `bot.handlers.info`: `router`, `support_text() -> str`, `paysupport_text() -> str`, `terms_text() -> str`.
  `bot.keyboards.inline.get_subscribe_kb(invoice_url: str, price: int, is_admin: bool = False)`.
  `bot.__main__.replay_pending_payments(bot, dp) -> None`, `run_polling(dp, bot) -> None`,
  `PUBLIC_COMMANDS: list[BotCommand]`.

- [ ] **Step 1: Настройки — `bot/config.py`**

Удалить поля `USDT_TRC20_ADDRESS`, `VN_BANK_DETAILS`, `TH_BANK_DETAILS` (вместе с комментарием над ними),
`SUBSCRIPTION_PRICE_USDT`, `SUBSCRIPTION_PRICE_VND`, `SUBSCRIPTION_PRICE_THB`. Добавить рядом с
`FREE_DOWNLOADS_PER_DAY`:

```python
    # Цена подписки на 30 дней в звёздах Telegram. 10000 — потолок Telegram для подписок.
    SUBSCRIPTION_PRICE_STARS: int = Field(default=250, ge=1, le=10000)
```

В докстринге `_within_one_edit` пример «USDT → UDST … `SUBSCRIPTION_PRICE_UDST`» заменить на
«STARS → STRAS … `SUBSCRIPTION_PRICE_STRAS`»; в докстринге `check_env_keys` пример
`USDT_TRC2O_ADDRESS` и «пустой адрес оплаты» заменить на `ADMIN_USERNAMF` и «в поддержке
показывается заглушка вместо ника администратора»; «молчаливо неверный адрес оплаты стоит денег» →
«молчаливо неверная настройка стоит денег».

`tests/test_config_validation.py`: в тесте про M-26 ключ `USDT_TRC2O_ADDRESS` → `ADMIN_USERNAMF`,
ожидаемое имя в сообщении `USDT_TRC20_ADDRESS` → `ADMIN_USERNAME` (докстринг теста поправить так же);
в тесте про перестановку `SUBSCRIPTION_PRICE_UDST`/`SUBSCRIPTION_PRICE_USDT` →
`SUBSCRIPTION_PRICE_STRAS`/`SUBSCRIPTION_PRICE_STARS` (и «USDT → UDST» в докстринге → «STARS → STRAS»).
Добавить тест диапазона:

```python
def test_subscription_price_is_bounded_by_telegram_limits():
    from pydantic import ValidationError

    from bot.config import Settings

    for bad in ("0", "10001"):
        with pytest.raises(ValidationError):
            Settings(SUBSCRIPTION_PRICE_STARS=bad)
```

(если `pytest` в файле не импортирован — добавить `import pytest`).

- [ ] **Step 2: Тесты команд — создать `tests/test_info_commands.py`**

```python
from bot.config import settings
from bot.handlers.info import paysupport_text, support_text, terms_text


def test_terms_names_price_renewal_refund_rule_and_contacts(monkeypatch):
    monkeypatch.setattr(settings, "SUBSCRIPTION_PRICE_STARS", 250)
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@owner")
    text = terms_text()
    assert "250 ⭐" in text
    assert "3 скачивания за любые 24 часа" in text
    assert "автоматически" in text
    assert "Мои звёзды" in text
    assert "/paysupport" in text and "/support" in text and "@owner" in text
    assert "администратор" in text


def test_paysupport_points_to_admin_and_terms(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@owner")
    text = paysupport_text()
    assert "@owner" in text and "/terms" in text and "дату платежа" in text


def test_texts_escape_admin_username(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@a<b>&c")
    for text in (support_text(), paysupport_text(), terms_text()):
        assert "@a&lt;b&gt;&amp;c" in text
        assert "<b>&c" not in text


def test_support_text_no_longer_mentions_payment_screenshot():
    assert "скриншот" not in support_text().lower()
```

`tests/test_user_escaping.py`: удалить тесты `test_payment_details_*` и импорт `_payment_details_text`;
тест `test_support_text_escapes_admin_username` перевести на `from bot.handlers.info import support_text`.

- [ ] **Step 3: Команды — создать `bot/handlers/info.py`**

```python
"""Команды, которых Telegram требует от бота с оплатой: /terms, /support, /paysupport."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import settings
from bot.utils.text import esc

router = Router(name="info")


def support_text() -> str:
    return (
        "✉️ <b>Связь с администратором</b>\n\n"
        f"Напиши администратору: {esc(settings.ADMIN_USERNAME)}\n\n"
        "Опиши проблему и приложи ссылку, которая не скачалась."
    )


def paysupport_text() -> str:
    return (
        "💳 <b>Оплата и возвраты</b>\n\n"
        f"Напиши администратору: {esc(settings.ADMIN_USERNAME)} — укажи дату платежа "
        "и что случилось.\n\n"
        "Возврат делаем, если бот не смог скачать то, ради чего ты оформил подписку, "
        "и поддержка не помогла. Подробно — /terms."
    )


def terms_text() -> str:
    admin = esc(settings.ADMIN_USERNAME)
    return (
        "📄 <b>Условия использования</b>\n\n"
        "1. <b>Что это.</b> Бот скачивает видео и фото по ссылкам из Instagram, TikTok, "
        "Facebook, Pinterest и YouTube и присылает их в чат.\n\n"
        f"2. <b>Бесплатно.</b> {settings.FREE_DOWNLOADS_PER_DAY} скачивания за любые 24 часа. "
        "Неудачное скачивание не считается.\n\n"
        f"3. <b>Подписка.</b> {settings.SUBSCRIPTION_PRICE_STARS} ⭐ за 30 дней безлимитных "
        "скачиваний, оплата звёздами Telegram. Продлевается автоматически каждые 30 дней, "
        "пока ты её не отменишь: настройки Telegram → «Мои звёзды». После отмены работает "
        "до конца оплаченного срока.\n\n"
        "4. <b>Возврат.</b> Если бот не смог скачать то, ради чего ты оформил подписку, и "
        "поддержка не помогла — напиши в /paysupport и укажи дату платежа. Решение о возврате "
        "принимает администратор. При возврате подписка отключается сразу.\n\n"
        "5. <b>Ограничения.</b> Скачивается только общедоступное: приватные, удалённые и "
        "закрытые в регионе публикации недоступны. Соцсети меняют свои сайты — отдельные "
        "ссылки могут временно не скачиваться. Бесперебойная работа 24/7 не гарантируется.\n\n"
        "6. <b>Контент.</b> Ты сам отвечаешь за использование скачанного: соблюдай авторские "
        "права и правила площадок. Бот не хранит файлы — они удаляются сразу после отправки.\n\n"
        "7. <b>Данные.</b> Храним твой Telegram ID, имя и ник, историю скачиваний (ссылки — "
        "до 180 дней) и платежей. Третьим лицам не передаём.\n\n"
        f"8. <b>Связь.</b> Вопросы — /support, оплата и возвраты — /paysupport или {admin}.\n\n"
        "9. Условия могут меняться; актуальная версия — по команде /terms."
    )


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    await message.answer(support_text())


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    await message.answer(paysupport_text())


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    await message.answer(terms_text())
```

Примечание: «3 скачивания» — при `FREE_DOWNLOADS_PER_DAY=3`; склонение для других значений не
требуется (YAGNI), тест проверяет значение по умолчанию.

`bot/handlers/user.py`: удалить `_payment_details_text`, `_support_text`, хендлер `cb_pay_details`;
в `cb_support` звать `support_text()` из `bot.handlers.info`.

- [ ] **Step 4: Тесты оплаты — создать `tests/test_payments_flow.py`**

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bot.handlers.payments as P
import bot.handlers.user as U
from bot.config import settings
from bot.db.models import User
from tests.test_user_handle_url import _make_session_maker

UID = 700000001


class _Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _query(currency="XTR", payload="sub_30d"):
    return SimpleNamespace(currency=currency, invoice_payload=payload, answer=_Recorder())


def _sp(charge="ch_1", first=True):
    return SimpleNamespace(
        telegram_payment_charge_id=charge,
        total_amount=250,
        invoice_payload="sub_30d",
        is_recurring=True,
        is_first_recurring=True if first else None,
        subscription_expiration_date=None,
    )


def _pay_message(payment, *, username="payer", full_name="Pay Er"):
    bot = SimpleNamespace(send_message=_Recorder())
    return SimpleNamespace(
        from_user=SimpleNamespace(id=UID, username=username, full_name=full_name),
        successful_payment=payment,
        bot=bot,
        answer=_Recorder(),
    )


# ── pre_checkout ──


async def test_pre_checkout_approves_stars_subscription_without_db(monkeypatch):
    def _no_db():
        raise AssertionError("pre_checkout must not touch the database")

    monkeypatch.setattr(P, "async_session", _no_db)
    query = _query()
    await P.on_pre_checkout(query)
    assert query.answer.calls == [((), {"ok": True})]


@pytest.mark.parametrize("currency,payload", [("USD", "sub_30d"), ("XTR", "other")])
async def test_pre_checkout_rejects_foreign_invoice(currency, payload):
    query = _query(currency, payload)
    await P.on_pre_checkout(query)
    (_, kwargs), = query.answer.calls
    assert kwargs["ok"] is False and "Подписка" in kwargs["error_message"]


# ── successful_payment ──


async def test_payment_activates_subscription_and_notifies(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "pay.db")
    monkeypatch.setattr(P, "async_session", maker)
    try:
        message = _pay_message(_sp())
        await P.on_successful_payment(message)

        assert "Подписка оформлена" in message.answer.calls[0][0][0]
        (admin_args, _), = message.bot.send_message.calls
        assert admin_args[0] == settings.ADMIN_ID and "250 ⭐" in admin_args[1]
        async with maker() as session:
            assert (await session.get(User, UID)).subscription_until is not None
    finally:
        await engine.dispose()


async def test_redelivered_payment_is_silent(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "dup.db")
    monkeypatch.setattr(P, "async_session", maker)
    try:
        await P.on_successful_payment(_pay_message(_sp()))
        again = _pay_message(_sp())
        await P.on_successful_payment(again)
        assert again.answer.calls == [] and again.bot.send_message.calls == []
    finally:
        await engine.dispose()


async def test_renewal_message_says_extended(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "renew.db")
    monkeypatch.setattr(P, "async_session", maker)
    try:
        await P.on_successful_payment(_pay_message(_sp()))
        renewal = _pay_message(_sp("ch_2", first=False))
        await P.on_successful_payment(renewal)
        assert "продлена" in renewal.answer.calls[0][0][0]
        assert "продление" in renewal.bot.send_message.calls[0][0][1]
    finally:
        await engine.dispose()


async def test_db_failure_alerts_admin_and_reassures_user(monkeypatch):
    def _broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(P, "async_session", _broken)
    message = _pay_message(_sp("ch_lost"))
    await P.on_successful_payment(message)

    assert message.answer.calls[0][0][0] == P.PAYMENT_PENDING_TEXT
    admin_text = message.bot.send_message.calls[0][0][1]
    assert "не записан" in admin_text and "ch_lost" in admin_text


async def test_admin_notice_escapes_name_and_handles_missing_username(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "esc.db")
    monkeypatch.setattr(P, "async_session", maker)
    try:
        message = _pay_message(_sp(), username=None, full_name="Ann <3")
        await P.on_successful_payment(message)
        admin_text = message.bot.send_message.calls[0][0][1]
        assert "Ann &lt;3" in admin_text and "без ника" in admin_text
    finally:
        await engine.dispose()


async def test_admin_blocked_bot_does_not_break_payment(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "blk.db")
    monkeypatch.setattr(P, "async_session", maker)

    async def _refuse(*a, **k):
        raise RuntimeError("bot was blocked by the user")

    try:
        message = _pay_message(_sp())
        message.bot.send_message = _refuse
        await P.on_successful_payment(message)  # не должно бросить
        assert "Подписка оформлена" in message.answer.calls[0][0][0]
    finally:
        await engine.dispose()


# ── ссылка-счёт ──


async def test_invoice_link_is_created_once_with_stars_subscription(monkeypatch):
    monkeypatch.setattr(P, "_invoice_link", None)
    created = _Recorder()

    async def _create(**kwargs):
        await created(**kwargs)
        return "https://t.me/$invoice"

    bot = SimpleNamespace(create_invoice_link=_create)
    assert await P.get_invoice_link(bot) == "https://t.me/$invoice"
    assert await P.get_invoice_link(bot) == "https://t.me/$invoice"

    (_, kwargs), = created.calls
    assert kwargs["currency"] == "XTR" and kwargs["provider_token"] == ""
    assert kwargs["subscription_period"] == 2592000
    assert kwargs["payload"] == "sub_30d"
    assert kwargs["prices"][0].amount == settings.SUBSCRIPTION_PRICE_STARS


# ── экран подписки ──


class _Callback:
    def __init__(self, uid=UID):
        self.from_user = SimpleNamespace(id=uid, username="u", full_name="U")
        self.bot = SimpleNamespace()
        self.answer = _Recorder()
        self.message = None


async def _subscribe_screen(monkeypatch, sqlite_engine_factory, tmp_path, *, until, link_error=None):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "sub.db")
    monkeypatch.setattr(U, "async_session", maker)
    async with maker() as session, session.begin():
        session.add(User(id=UID, username="u", full_name="U", subscription_until=until))
    shown = []

    async def _edit(callback, text, reply_markup=None):
        shown.append((text, reply_markup))

    async def _link(bot):
        if link_error:
            raise link_error
        return "https://t.me/$invoice"

    monkeypatch.setattr(U, "_safe_edit", _edit)
    monkeypatch.setattr(U, "get_invoice_link", _link)
    await U.cb_subscribe(_Callback())
    await engine.dispose()
    return shown[0]


def _urls(markup):
    return [b.url for row in markup.inline_keyboard for b in row if b.url]


async def test_subscribe_screen_offers_stars_purchase(monkeypatch, sqlite_engine_factory, tmp_path):
    text, markup = await _subscribe_screen(monkeypatch, sqlite_engine_factory, tmp_path, until=None)
    assert f"{settings.SUBSCRIPTION_PRICE_STARS} ⭐" in text and "/terms" in text
    assert _urls(markup) == ["https://t.me/$invoice"]


async def test_subscribe_screen_hides_purchase_while_active(monkeypatch, sqlite_engine_factory, tmp_path):
    until = datetime.now(timezone.utc) + timedelta(days=5)
    text, markup = await _subscribe_screen(monkeypatch, sqlite_engine_factory, tmp_path, until=until)
    assert "активна" in text and until.strftime("%d.%m.%Y") in text
    assert _urls(markup) == []


async def test_subscribe_screen_offers_purchase_after_expiry(monkeypatch, sqlite_engine_factory, tmp_path):
    until = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, markup = await _subscribe_screen(monkeypatch, sqlite_engine_factory, tmp_path, until=until)
    assert _urls(markup) == ["https://t.me/$invoice"]


async def test_subscribe_screen_survives_invoice_failure(monkeypatch, sqlite_engine_factory, tmp_path):
    text, markup = await _subscribe_screen(
        monkeypatch, sqlite_engine_factory, tmp_path, until=None, link_error=RuntimeError("net")
    )
    assert "временно недоступна" in text
    assert _urls(markup) == []
```

- [ ] **Step 5: Хендлеры оплаты — создать `bot/handlers/payments.py`**

```python
"""Оплата подписки звёздами Telegram: ссылка-счёт, проверка перед оплатой, зачисление."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery
from loguru import logger

from bot.config import settings
from bot.db.engine import async_session
from bot.db.payments import PaymentOutcome, apply_star_payment
from bot.db.queries import get_or_create_user
from bot.utils.text import esc

router = Router(name="payments")

STARS_CURRENCY = "XTR"
SUBSCRIPTION_PAYLOAD = "sub_30d"
# 30 дней — единственный период подписки, который сейчас принимает Telegram.
SUBSCRIPTION_PERIOD_SECONDS = 2592000
INVOICE_TITLE = "Подписка на 30 дней"
INVOICE_DESCRIPTION = (
    "Безлимитные скачивания из Instagram, TikTok, Facebook, Pinterest и YouTube. "
    "Продлевается автоматически каждые 30 дней."
)
CANCEL_HINT = "отменить автопродление можно в настройках Telegram → «Мои звёзды»"
STALE_INVOICE_TEXT = "Счёт устарел — открой «👑 Подписка» в боте и оплати заново."
PAYMENT_PENDING_TEXT = (
    "✅ Оплата получена. Подписку включит администратор в ближайшее время; "
    "вопросы — /paysupport."
)
USER_DATE_FORMAT = "%d.%m.%Y"
ADMIN_DATE_FORMAT = "%d.%m.%Y %H:%M UTC"

# Ссылка-счёт общая для всех: плательщика Telegram присылает в successful_payment.
# Создаём один раз на процесс — лишний запрос к Telegram на каждое открытие экрана не нужен.
_invoice_link: str | None = None


async def get_invoice_link(bot) -> str:
    global _invoice_link
    if _invoice_link is None:
        _invoice_link = await bot.create_invoice_link(
            title=INVOICE_TITLE,
            description=INVOICE_DESCRIPTION,
            payload=SUBSCRIPTION_PAYLOAD,
            currency=STARS_CURRENCY,
            prices=[LabeledPrice(label=INVOICE_TITLE, amount=settings.SUBSCRIPTION_PRICE_STARS)],
            provider_token="",
            subscription_period=SUBSCRIPTION_PERIOD_SECONDS,
        )
    return _invoice_link


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Отвечаем сразу и без базы: у Telegram на это 10 секунд.

    Цену и наличие подписки НЕ сверяем. Документация не говорит, идут ли
    продления через эту проверку; если идут — сверка с текущей ценой или
    отказ «подписка уже есть» сломали бы продления всем подписчикам.
    """
    if query.currency == STARS_CURRENCY and query.invoice_payload == SUBSCRIPTION_PAYLOAD:
        await query.answer(ok=True)
        return
    await query.answer(ok=False, error_message=STALE_INVOICE_TEXT)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    try:
        async with async_session() as session, session.begin():
            user = await get_or_create_user(session, message.from_user)
            outcome = await apply_star_payment(session, user_id=user.id, payment=payment)
    except Exception:
        logger.exception(
            "Платёж не записан | user={} charge={}",
            message.from_user.id, payment.telegram_payment_charge_id,
        )
        await notify_admin(message.bot, _payment_lost_text(message, payment))
        await _answer_quietly(message, PAYMENT_PENDING_TEXT)
        return
    if outcome.duplicate:
        logger.info("Повторная доставка платежа | charge={}", payment.telegram_payment_charge_id)
        return
    await _answer_quietly(message, _payment_done_text(outcome))
    await notify_admin(message.bot, _payment_admin_text(message, payment, outcome))


def _payment_done_text(outcome: PaymentOutcome) -> str:
    until = outcome.subscription_until.strftime(USER_DATE_FORMAT)
    if outcome.is_first:
        return (
            f"✅ <b>Подписка оформлена!</b>\nБезлимит до <b>{until}</b>.\n"
            f"Она продлится автоматически; {CANCEL_HINT}."
        )
    return f"🔄 Подписка продлена до <b>{until}</b>."


def _payment_admin_text(message: Message, payment, outcome: PaymentOutcome) -> str:
    user = message.from_user
    handle = f"@{esc(user.username)}" if user.username else "без ника"
    kind = "первый платёж" if outcome.is_first else "продление"
    until = outcome.subscription_until.strftime(ADMIN_DATE_FORMAT)
    return (
        f"💰 <b>{payment.total_amount} ⭐</b> — {esc(user.full_name)} ({handle}, "
        f"<code>{user.id}</code>)\n{kind}, подписка до {until}"
    )


def _payment_lost_text(message: Message, payment) -> str:
    return (
        "⚠️ <b>Платёж не записан в базу</b>\n"
        f"Пользователь <code>{message.from_user.id}</code>, {payment.total_amount} ⭐\n"
        f"Чек: <code>{esc(payment.telegram_payment_charge_id)}</code>\n"
        "Выдай 30 дней вручную."
    )


async def notify_admin(bot, text: str) -> None:
    """Сообщение админу; его недоступность не должна ломать зачисление."""
    try:
        await bot.send_message(settings.ADMIN_ID, text)
    except Exception as exc:
        logger.warning("Не удалось уведомить админа | error={}", exc)


async def _answer_quietly(message: Message, text: str) -> None:
    try:
        await message.answer(text)
    except Exception as exc:
        logger.info("Не удалось ответить плательщику | user={} error={}", message.from_user.id, exc)
```

- [ ] **Step 6: Экран подписки — `bot/handlers/user.py` и клавиатуры**

`bot/keyboards/inline.py`: удалить `get_payment_details_kb`; `get_paywall_kb` и новая `get_subscribe_kb`:

```python
def get_paywall_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👑 Оформить подписку", callback_data="menu:subscribe")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_subscribe_kb(invoice_url: str, price: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"⭐ Оформить за {price} ⭐", url=invoice_url)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

`bot/handlers/user.py`: импорты — `get_subscribe_kb` из клавиатур (убрать `get_payment_details_kb`),
`from bot.handlers.payments import CANCEL_HINT, get_invoice_link`. Заменить `cb_subscribe` целиком:

```python
PAYMENT_UNAVAILABLE_TEXT = "⚠️ Оплата временно недоступна, попробуй позже."


def _subscribe_text(sub_until: datetime | None) -> str:
    if sub_until is not None:
        return (
            f"👑 <b>Подписка активна</b> до <b>{sub_until.strftime('%d.%m.%Y')}</b>.\n\n"
            f"Если оформлена звёздами — продлится сама; {CANCEL_HINT}."
        )
    return (
        "👑 <b>Подписка</b>\n\n"
        f"Безлимитные скачивания на 30 дней — <b>{settings.SUBSCRIPTION_PRICE_STARS} ⭐</b>.\n"
        f"Продлевается автоматически каждые 30 дней; {CANCEL_HINT}.\n\n"
        "Оплачивая, ты принимаешь условия: /terms"
    )


@router.callback_query(F.data == "menu:subscribe")
async def cb_subscribe(callback: CallbackQuery) -> None:
    """Кнопки покупки при активной подписке нет: Telegram разрешает одному
    человеку несколько подписок сразу — это было бы двойное списание."""
    await callback.answer()
    is_admin = _is_admin(callback.from_user.id)
    async with async_session() as session, session.begin():
        sub_until = _active_subscription_until(await get_or_create_user(session, callback.from_user))
    if sub_until is not None:
        await _safe_edit(callback, _subscribe_text(sub_until), reply_markup=get_back_to_menu_kb(is_admin=is_admin))
        return
    try:
        link = await get_invoice_link(callback.bot)
    except Exception as exc:
        logger.error("Не удалось создать ссылку на оплату | error={}", exc)
        await _safe_edit(callback, PAYMENT_UNAVAILABLE_TEXT, reply_markup=get_back_to_menu_kb(is_admin=is_admin))
        return
    await _safe_edit(
        callback,
        _subscribe_text(None),
        reply_markup=get_subscribe_kb(link, settings.SUBSCRIPTION_PRICE_STARS, is_admin=is_admin),
    )
```

Проверить: в `user.py` не осталось `USDT`, `VN_BANK`, `TH_BANK`, `pay:show_details`, `скриншот`.

- [ ] **Step 7: Антифлуд — тест и правка**

Добавить в `tests/test_throttle.py`:

```python
class FakePaymentEvent(FakeEvent):
    def __init__(self, user_id=1000000001):
        super().__init__(user_id)
        self.successful_payment = object()


async def test_payment_is_never_throttled():
    """Звёзды уже списаны — отбросить известие о платеже значит не продлить подписку."""
    mw = ThrottleMiddleware(rate_limit=10.0, notify=True)
    await mw(_passthrough, FakeEvent(), {})
    event = FakePaymentEvent()
    assert await mw(_passthrough, event, {}) == "handled"
    assert event.answers == []
```

`bot/middlewares/throttle.py`, начало `__call__` (перед `user = getattr(...)`):

```python
        if getattr(event, "successful_payment", None) is not None:
            # Звёзды уже списаны: отброшенное известие = оплаченная, но не
            # продлённая подписка. Платёж идёт к хендлеру всегда.
            return await handler(event, data)
```

- [ ] **Step 8: Очередь простоя, команды, роутеры — тесты**

Переписать `tests/test_polling_startup.py` целиком:

```python
from types import SimpleNamespace

from bot import __main__ as entrypoint


def _update(update_id, *, payment=False):
    message = SimpleNamespace(successful_payment=object() if payment else None)
    return SimpleNamespace(update_id=update_id, message=message)


class FakeBot:
    def __init__(self, batches, fail=False):
        self.batches = list(batches)
        self.fail = fail
        self.offsets = []
        self.webhook_drops = []

    async def delete_webhook(self, drop_pending_updates):
        self.webhook_drops.append(drop_pending_updates)

    async def get_updates(self, offset=None, timeout=None, limit=None, allowed_updates=None):
        if self.fail:
            raise RuntimeError("network down")
        self.offsets.append(offset)
        return self.batches.pop(0) if self.batches else []


class FakeDispatcher:
    def __init__(self):
        self.fed = []
        self.polling_kwargs = None

    def resolve_used_update_types(self):
        return ["message", "callback_query", "pre_checkout_query"]

    async def feed_update(self, bot, update):
        self.fed.append(update.update_id)

    async def start_polling(self, bot, **kwargs):
        self.polling_kwargs = kwargs


async def test_backlog_replays_only_payments_and_confirms_offset():
    bot = FakeBot([[_update(5, payment=True), _update(6)], [_update(7)]])
    dp = FakeDispatcher()

    await entrypoint.replay_pending_payments(bot, dp)

    assert dp.fed == [5]  # ссылка из простоя (6, 7) не качается
    assert bot.offsets == [None, 7, 8]  # последний запрос подтверждает выброс
    assert bot.webhook_drops == [False]


async def test_polling_starts_without_dropping_after_replay():
    dp = FakeDispatcher()
    await entrypoint.run_polling(dp, FakeBot([]))
    assert dp.polling_kwargs.get("drop_pending_updates") in (None, False)


async def test_replay_failure_falls_back_to_dropping_backlog():
    dp = FakeDispatcher()
    await entrypoint.run_polling(dp, FakeBot([], fail=True))
    assert dp.polling_kwargs.get("drop_pending_updates") is True
    assert dp.fed == []


def test_public_commands_include_payment_requirements():
    names = {c.command for c in entrypoint.PUBLIC_COMMANDS}
    assert {"start", "terms", "support", "paysupport"} <= names
```

- [ ] **Step 9: `bot/__main__.py` и `bot/handlers/__init__.py`**

`bot/handlers/__init__.py`:

```python
from bot.handlers.admin import router as admin_router
from bot.handlers.info import router as info_router
from bot.handlers.payments import router as payments_router
from bot.handlers.user import router as user_router
```

`bot/__main__.py`: импорт роутеров дополнить `info_router, payments_router`; `run_polling` заменить,
добавить `replay_pending_payments`, `PUBLIC_COMMANDS`, `BACKLOG_BATCH`:

```python
BACKLOG_BATCH = 100

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="🏠 Главное меню"),
    BotCommand(command="terms", description="📄 Условия использования"),
    BotCommand(command="support", description="✉️ Поддержка"),
    BotCommand(command="paysupport", description="💳 Вопросы по оплате"),
]


async def replay_pending_payments(bot, dp) -> None:
    """Очередь, накопившаяся за простой: платежи обработать, остальное выбросить.

    Раньше очередь сбрасывалась целиком (`drop_pending_updates=True`), чтобы
    ссылки из простоя не качались заново со списанием квоты. Но продление
    подписки Telegram списывает сам в любой момент, и известие, пришедшее
    во время перезапуска, терялось бы вместе со ссылками. Запрос с
    `offset = последний + 1` подтверждает выброс остального.
    """
    await bot.delete_webhook(drop_pending_updates=False)
    allowed = dp.resolve_used_update_types()
    offset = None
    while True:
        updates = await bot.get_updates(
            offset=offset, timeout=0, limit=BACKLOG_BATCH, allowed_updates=allowed
        )
        if not updates:
            return
        for update in updates:
            if update.message is not None and update.message.successful_payment is not None:
                await dp.feed_update(bot, update)
        offset = updates[-1].update_id + 1


async def run_polling(dp, bot) -> None:
    """Запуск лонг-поллинга после разбора очереди простоя.

    Если разбор не удался (сеть), ведём себя как раньше — сбрасываем очередь
    целиком: старые ссылки качать нельзя, а упасть на старте хуже.
    """
    try:
        await replay_pending_payments(bot, dp)
    except Exception as exc:
        logger.warning("Разбор очереди простоя не удался, сбрасываем её | error={}", exc)
        await dp.start_polling(bot, drop_pending_updates=True)
        return
    await dp.start_polling(bot)
```

В `main()`: `dp.include_router(...)` в порядке `admin_router`, `payments_router`, `info_router`, `user_router`
(у `user_router` текстовый catch-all — он последним). Команды:

```python
    await bot.set_my_commands(PUBLIC_COMMANDS)
    from aiogram.types import BotCommandScopeChat
    try:
        await bot.set_my_commands(
            [*PUBLIC_COMMANDS, BotCommand(command="admin", description="⚙️  Админ-панель")],
            scope=BotCommandScopeChat(chat_id=settings.ADMIN_ID),
        )
    except Exception:
        pass
```

- [ ] **Step 10: Прогон и коммит**

Run: `./scripts/test.sh -q` → всё зелёное.
`grep -rn "USDT\|VN_BANK\|TH_BANK\|SUBSCRIPTION_PRICE_\(USDT\|VND\|THB\)\|pay:show_details\|payment_details" bot/ tests/` → пусто.

```bash
git add bot/ tests/
git commit -m "feat(payments): sell the subscription for Telegram Stars with auto-renewal"
```

---

### Task 4: Возврат платежа из админки

**Files:**
- Create: `bot/handlers/admin_payments.py`
- Modify: `bot/keyboards/inline.py` (`get_user_card_kb` + «💸 Платежи», `get_payments_kb`, `get_refund_confirm_kb`)
- Modify: `bot/handlers/__init__.py`, `bot/__main__.py` (подключить роутер после `admin_router`)
- Test: create `tests/test_admin_refund.py`

**Interfaces:**
- Consumes: `list_user_payments`, `get_payment`, `apply_refund` (Task 2); из `bot/handlers/admin.py` —
  `is_admin`, `_safe_edit`, `_format_dt`, `_card_text` (Task 1), `get_user_by_id`.
- Produces: `bot.handlers.admin_payments.router`, `PAYMENTS_IN_CARD = 10`,
  `ALREADY_REFUNDED_MARKER = "CHARGE_ALREADY_REFUNDED"`, хендлеры `cb_payments`, `cb_refund_ask`,
  `cb_refund_do`, `cb_card`; `get_payments_kb(user_id, payments)`, `get_refund_confirm_kb(payment_id, user_id)`.
  Формат `callback_data`: `admin:payments:<user_id>`, `admin:refund_ask:<payment_id>`,
  `admin:refund_do:<payment_id>`, `admin:card:<user_id>`.

- [ ] **Step 1: Тесты — создать `tests/test_admin_refund.py`**

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

import bot.handlers.admin_payments as AP
from bot.config import settings
from bot.db.models import StarPayment, User
from bot.keyboards.inline import get_payments_kb, get_user_card_kb
from tests.test_user_handle_url import _make_session_maker

UID = 600000001


class FakeBot:
    def __init__(self, refund_error=None, cancel_error=None):
        self.refund_error = refund_error
        self.cancel_error = cancel_error
        self.cancelled = []
        self.refunded = []
        self.sent = []

    async def edit_user_star_subscription(self, user_id, telegram_payment_charge_id, is_canceled):
        self.cancelled.append((user_id, telegram_payment_charge_id, is_canceled))
        if self.cancel_error:
            raise self.cancel_error

    async def refund_star_payment(self, user_id, telegram_payment_charge_id):
        self.refunded.append((user_id, telegram_payment_charge_id))
        if self.refund_error:
            raise self.refund_error

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)


class FakeCallback:
    def __init__(self, data, bot, uid=None):
        self.data = data
        self.bot = bot
        self.from_user = SimpleNamespace(id=settings.ADMIN_ID if uid is None else uid)
        self.message = None  # _safe_edit шлёт новым сообщением через bot.send_message
        self.toasts = []

    async def answer(self, text=None, **kwargs):
        self.toasts.append(text)


def _bad_request(text):
    return TelegramBadRequest(method=None, message=text)


async def _setup(monkeypatch, sqlite_engine_factory, tmp_path, *, refunded=False):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "refund.db")
    monkeypatch.setattr(AP, "async_session", maker)
    async with maker() as session, session.begin():
        session.add(User(id=UID, username="p", full_name="P",
                         subscription_until=datetime(2026, 10, 26, tzinfo=timezone.utc)))
        await session.flush()
        session.add(StarPayment(
            id=1, user_id=UID, telegram_payment_charge_id="ch_first", subscription_charge_id="ch_first",
            amount=250, invoice_payload="sub_30d", is_first=True,
            refunded_at=datetime.now(timezone.utc) if refunded else None,
        ))
        await session.flush()
        session.add(StarPayment(
            id=2, user_id=UID, telegram_payment_charge_id="ch_renew", subscription_charge_id="ch_first",
            amount=250, invoice_payload="sub_30d", is_first=False,
        ))
    return maker, engine


async def _state(maker, payment_id):
    async with maker() as session:
        return (await session.get(User, UID)).subscription_until, (await session.get(StarPayment, payment_id)).refunded_at


async def test_refund_cancels_renewal_by_first_charge_and_removes_subscription(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot()
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot))
        assert bot.cancelled == [(UID, "ch_first", True)]
        assert bot.refunded == [(UID, "ch_renew")]
        until, refunded_at = await _state(maker, 2)
        assert until is None and refunded_at is not None
    finally:
        await engine.dispose()


async def test_already_refunded_in_telegram_counts_as_success(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot(refund_error=_bad_request("Bad Request: CHARGE_ALREADY_REFUNDED"))
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot))
        until, refunded_at = await _state(maker, 2)
        assert until is None and refunded_at is not None
    finally:
        await engine.dispose()


async def test_telegram_refusal_leaves_database_untouched(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot(refund_error=_bad_request("Bad Request: CHARGE_NOT_FOUND"))
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot))
        until, refunded_at = await _state(maker, 2)
        assert until is not None and refunded_at is None
        assert any("CHARGE_NOT_FOUND" in text for text in bot.sent)
    finally:
        await engine.dispose()


async def test_cancel_failure_does_not_block_refund(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot(cancel_error=_bad_request("Bad Request: SUBSCRIPTION_NOT_ACTIVE"))
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot))
        assert bot.refunded == [(UID, "ch_renew")]
        assert (await _state(maker, 2))[1] is not None
    finally:
        await engine.dispose()


async def test_refunded_payment_is_not_refunded_again(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path, refunded=True)
    try:
        bot = FakeBot()
        callback = FakeCallback("admin:refund_do:1", bot)
        await AP.cb_refund_do(callback)
        assert bot.refunded == [] and bot.cancelled == []
        assert "Уже возвращён" in callback.toasts
    finally:
        await engine.dispose()


async def test_non_admin_cannot_refund(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot()
        await AP.cb_refund_do(FakeCallback("admin:refund_do:2", bot, uid=424242))
        assert bot.refunded == [] and bot.sent == []
    finally:
        await engine.dispose()


async def test_garbage_callback_is_answered_quietly(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        for data in ("admin:refund_do:abc", "admin:refund_ask:", "admin:payments:x", "admin:card:"):
            callback = FakeCallback(data, FakeBot())
            handler = {
                "admin:refund_do": AP.cb_refund_do, "admin:refund_ask": AP.cb_refund_ask,
                "admin:payments": AP.cb_payments, "admin:card": AP.cb_card,
            }[data.rsplit(":", 1)[0]]
            await handler(callback)
            assert callback.toasts == [None]
    finally:
        await engine.dispose()


async def test_payments_screen_lists_and_offers_refund_only_for_unrefunded(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path, refunded=True)
    try:
        bot = FakeBot()
        await AP.cb_payments(FakeCallback(f"admin:payments:{UID}", bot))
        assert "250 ⭐" in bot.sent[0] and "возвращён" in bot.sent[0]
    finally:
        await engine.dispose()


async def test_payments_screen_for_user_without_payments(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _setup(monkeypatch, sqlite_engine_factory, tmp_path)
    try:
        bot = FakeBot()
        await AP.cb_payments(FakeCallback("admin:payments:999", bot))
        assert "нет" in bot.sent[0]
    finally:
        await engine.dispose()


def _callback_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_refund_buttons_only_for_unrefunded_payments_and_fit_limit():
    payments = [
        SimpleNamespace(id=10, amount=250, refunded_at=None, created_at=datetime(2026, 9, 26)),
        SimpleNamespace(id=11, amount=250, refunded_at=datetime(2026, 9, 27), created_at=datetime(2026, 9, 20)),
    ]
    data = _callback_data(get_payments_kb(9999999999999999, payments))
    assert "admin:refund_ask:10" in data and "admin:refund_ask:11" not in data
    assert "admin:card:9999999999999999" in data
    assert all(len(d.encode()) <= 64 for d in data)


def test_user_card_has_payments_button():
    assert "admin:payments:42" in _callback_data(get_user_card_kb(42))
```

Прогон: `./scripts/test.sh tests/test_admin_refund.py -q` → FAIL (нет модуля).

- [ ] **Step 2: Клавиатуры — `bot/keyboards/inline.py`**

В `get_user_card_kb` перед строкой «🔨 Бан/Разбан» добавить
`[InlineKeyboardButton(text="💸 Платежи", callback_data=f"admin:payments:{user_id}")],`. Добавить:

```python
def get_payments_kb(user_id: int, payments) -> InlineKeyboardMarkup:
    """Кнопка возврата — только у невозвращённых платежей."""
    rows = [
        [InlineKeyboardButton(
            text=f"↩️ Вернуть {p.amount} ⭐ от {p.created_at:%d.%m}",
            callback_data=f"admin:refund_ask:{p.id}",
        )]
        for p in payments
        if p.refunded_at is None
    ]
    rows.append([InlineKeyboardButton(text="👤 Карточка", callback_data=f"admin:card:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_refund_confirm_kb(payment_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Вернуть", callback_data=f"admin:refund_do:{payment_id}")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data=f"admin:payments:{user_id}")],
    ])
```

- [ ] **Step 3: Хендлеры — создать `bot/handlers/admin_payments.py`**

```python
"""Админка: платежи пользователя и возврат звёзд с отменой автопродления."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from loguru import logger

from bot.db.engine import async_session
from bot.db.payments import apply_refund, get_payment, list_user_payments
from bot.db.queries import get_user_by_id
from bot.handlers.admin import _card_text, _format_dt, _safe_edit, is_admin
from bot.keyboards.inline import (
    get_admin_menu_kb,
    get_payments_kb,
    get_refund_confirm_kb,
    get_user_card_kb,
)
from bot.utils.text import esc

router = Router(name="admin_payments")

PAYMENTS_IN_CARD = 10
# Повторный возврат того же платежа Telegram отклоняет этой ошибкой — деньги уже у человека.
ALREADY_REFUNDED_MARKER = "CHARGE_ALREADY_REFUNDED"


def _tail_id(data: str) -> int | None:
    """Число после последнего двоеточия; мусор → None."""
    try:
        return int(data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _payment_line(payment) -> str:
    kind = "первый" if payment.is_first else "продление"
    status = " · ↩️ возвращён" if payment.refunded_at else ""
    return f"{_format_dt(payment.created_at)} · {payment.amount} ⭐ · {kind}{status}"


def _payments_text(user_id: int, payments) -> str:
    header = f"💸 <b>Платежи</b> <code>{user_id}</code>\n\n"
    if not payments:
        return header + "Платежей звёздами нет."
    return header + "\n".join(_payment_line(p) for p in payments)


async def _show_payments(callback: CallbackQuery, user_id: int) -> None:
    async with async_session() as session, session.begin():
        payments = await list_user_payments(session, user_id, PAYMENTS_IN_CARD)
    await _safe_edit(callback, _payments_text(user_id, payments), reply_markup=get_payments_kb(user_id, payments))


async def _load_payment(callback: CallbackQuery):
    """Платёж из callback_data или None (тогда колбэк уже отвечен)."""
    payment_id = _tail_id(callback.data)
    payment = None
    if payment_id is not None:
        async with async_session() as session, session.begin():
            payment = await get_payment(session, payment_id)
    if payment is None:
        await callback.answer()
    return payment


@router.callback_query(F.data.startswith("admin:payments:"))
async def cb_payments(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    user_id = _tail_id(callback.data)
    if user_id is not None:
        await _show_payments(callback, user_id)


@router.callback_query(F.data.startswith("admin:refund_ask:"))
async def cb_refund_ask(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    payment = await _load_payment(callback)
    if payment is None:
        return
    if payment.refunded_at is not None:
        await callback.answer("Уже возвращён")
        return
    await callback.answer()
    text = (
        f"Вернуть <b>{payment.amount} ⭐</b> за платёж от {_format_dt(payment.created_at)}?\n\n"
        "Подписка будет снята, автопродление отключено."
    )
    await _safe_edit(callback, text, reply_markup=get_refund_confirm_kb(payment.id, payment.user_id))


@router.callback_query(F.data.startswith("admin:refund_do:"))
async def cb_refund_do(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    payment = await _load_payment(callback)
    if payment is None:
        return
    if payment.refunded_at is not None:
        await callback.answer("Уже возвращён")
        return
    error = await _refund_in_telegram(callback.bot, payment)
    if error is not None:
        await callback.answer()
        await _safe_edit(
            callback,
            f"❌ Telegram отказал в возврате:\n<code>{esc(error)}</code>",
            reply_markup=get_refund_confirm_kb(payment.id, payment.user_id),
        )
        return
    async with async_session() as session, session.begin():
        await apply_refund(session, payment_id=payment.id, admin_id=callback.from_user.id)
    logger.info("Возврат звёзд | payment={} user={} amount={}", payment.id, payment.user_id, payment.amount)
    await callback.answer("✅ Возвращено")
    await _show_payments(callback, payment.user_id)


async def _refund_in_telegram(bot, payment) -> str | None:
    """None — звёзды у человека (вернули сейчас или раньше); иначе текст отказа.

    Сначала отменяем автопродление (по номеру ПЕРВОГО платежа — иначе Telegram
    его не найдёт). Его отказ не останавливает возврат: подписка могла уже
    закончиться или быть отменена самим пользователем.
    """
    try:
        await bot.edit_user_star_subscription(
            user_id=payment.user_id,
            telegram_payment_charge_id=payment.subscription_charge_id,
            is_canceled=True,
        )
    except TelegramAPIError as exc:
        logger.warning("Автопродление не отменено | payment={} error={}", payment.id, exc)
    try:
        await bot.refund_star_payment(
            user_id=payment.user_id, telegram_payment_charge_id=payment.telegram_payment_charge_id
        )
    except TelegramAPIError as exc:
        if ALREADY_REFUNDED_MARKER in str(exc).upper():
            return None
        logger.warning("Возврат отклонён Telegram | payment={} error={}", payment.id, exc)
        return str(exc)
    return None


@router.callback_query(F.data.startswith("admin:card:"))
async def cb_card(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    user_id = _tail_id(callback.data)
    if user_id is None:
        return
    async with async_session() as session, session.begin():
        user = await get_user_by_id(session, user_id)
        text = await _card_text(session, user) if user else None
    if text is None:
        await _safe_edit(callback, "❌ Пользователь не найден.", reply_markup=get_admin_menu_kb())
        return
    await _safe_edit(callback, text, reply_markup=get_user_card_kb(user_id))
```

Проверка контракта теста `test_garbage_callback_is_answered_quietly`: на мусоре каждый из четырёх
хендлеров вызывает `callback.answer()` ровно один раз без текста и ничего не шлёт.

- [ ] **Step 4: Подключить роутер**

`bot/handlers/__init__.py`: `from bot.handlers.admin_payments import router as admin_payments_router`.
`bot/__main__.py`: импорт + `dp.include_router(admin_payments_router)` сразу после `admin_router`.

- [ ] **Step 5: Прогон и коммит**

Run: `./scripts/test.sh -q` → всё зелёное.

```bash
git add bot/handlers/admin_payments.py bot/handlers/__init__.py bot/__main__.py bot/keyboards/inline.py tests/test_admin_refund.py
git commit -m "feat(admin): refund a Stars payment and cancel its auto-renewal"
```

---

### Task 5: README (RU + EN) и `.env.example`

**Files:**
- Modify: `README.md` (русский и английский разделы), `.env.example`, `.gitignore`

Независима от Task 4 по файлам — может идти параллельно с ней.

- [ ] **Step 1: `.env.example`**

Удалить блок «── Платёжные реквизиты … ──» (3 строки + заголовок) и закомментированные
`# FREE_DOWNLOADS=3`, `# SUBSCRIPTION_PRICE_USDT=…`, `# SUBSCRIPTION_PRICE_VND=…`,
`# SUBSCRIPTION_PRICE_THB=…`. В блок «Лимиты / цены» добавить:

```
# Бесплатные скачивания за скользящие 24 часа
# FREE_DOWNLOADS_PER_DAY=3
# Цена подписки на 30 дней в звёздах Telegram (1…10000), с автопродлением
# SUBSCRIPTION_PRICE_STARS=250
```

- [ ] **Step 2: README — русская часть**

- «Возможности для пользователя»: строки про «3 бесплатных скачивания для новых пользователей» и
  «Мультивалютная оплата USDT/VND/THB» заменить на:
  - «**3 бесплатных скачивания в сутки** (скользящие 24 часа); неудачное скачивание не списывается.
    Когда бесплатные кончились, бот говорит, через сколько откроется следующее.»
  - «**Подписка за звёзды Telegram** — 250 ⭐ за 30 дней безлимита, продлевается автоматически;
    отменить можно в настройках Telegram → «Мои звёзды». Оплата прямо в Telegram, без реквизитов
    и скриншотов.»
  - «Команды `/terms` (условия), `/support` (поддержка), `/paysupport` (вопросы по оплате и возвратам).»
  - строку про раздел «Профиль» — «сколько бесплатных осталось на сутки и через сколько откроется
    следующее, до какой даты активна подписка, сколько всего скачано».
- «Админ-панель»: добавить «Платежи пользователя звёздами и возврат в два нажатия — возврат сразу
  отменяет автопродление и снимает подписку.»; «Уведомление админу о каждой оплате и продлении.»
- «Технические особенности»: добавить пункт «Надёжная оплата: платёж не теряется ни антифлудом,
  ни перезапуском бота (очередь простоя разбирается — платежи зачисляются, старые ссылки
  выбрасываются); повторная доставка платежа не продлевает подписку дважды; бесплатный лимит
  бронируется атомарно одной командой базы.»
- «Структура проекта»: добавить `bot/db/free_quota.py`, `bot/db/payments.py`,
  `bot/handlers/payments.py`, `bot/handlers/info.py`, `bot/handlers/admin_payments.py`,
  `scripts/migrate_20260926.py` с однострочными описаниями в стиле соседних строк.
- «Конфигурация»: строку «Платёжные реквизиты» заменить двумя: `FREE_DOWNLOADS_PER_DAY` —
  «Бесплатных скачиваний за скользящие сутки (по умолчанию 3)», `SUBSCRIPTION_PRICE_STARS` —
  «Цена подписки на 30 дней в звёздах (по умолчанию 250)».
- Если в README есть раздел обновления/установки — добавить абзац «Обновление с версии до
  2026-09-26: остановить бота, выполнить `scripts/migrate_20260926.py --database … --backup-dir …`,
  запустить. Без миграции бот не стартует и прямо об этом пишет.»

- [ ] **Step 3: README — English part**, те же изменения по смыслу:
  «**3 free downloads per day** (rolling 24 hours); a failed download is not counted…»,
  «**Subscription paid in Telegram Stars** — 250 ⭐ for 30 days of unlimited downloads, auto-renewing;
  cancel any time in Telegram Settings → My Stars…», commands `/terms`, `/support`, `/paysupport`,
  admin refunds, the reliability bullet, project structure, configuration rows, upgrade note.

- [ ] **Step 4: `.gitignore`** — в блок «Локальные инструменты / приватное» добавить строку `HANDOFF.md`.

- [ ] **Step 5: Проверка и коммит**

`grep -n "USDT\|VND\|THB\|реквизит\|скриншот" README.md .env.example` → пусто (кроме, возможно,
исторических упоминаний в разделе «Портфолио» — их не должно быть).
`git ls-files | grep -E '^\.claude/|^\.superpowers/'` → пусто.

```bash
git add README.md .env.example .gitignore
git commit -m "docs(readme): describe Stars subscription and the daily free limit"
```

---

## Самопроверка плана по спеке

| Спека | Задача |
|---|---|
| §4 настройки | Task 1 (`FREE_DOWNLOADS_PER_DAY`), Task 3 (`SUBSCRIPTION_PRICE_STARS`, удаление реквизитов) |
| §5.1–5.3 данные и запросы | Task 1 (`free_download`), Task 2 (`star_payment`) |
| §5.4 миграция + страховка | Task 1 |
| §6.1 экран подписки | Task 3 |
| §6.2 pre_checkout | Task 3 |
| §6.3 successful_payment + уведомления | Task 3 |
| §6.4 антифлуд | Task 3 |
| §6.5 очередь простоя | Task 3 |
| §6.6 команды | Task 3 |
| §6.7 лимит в интерфейсе | Task 1 (+ клавиатура отказа — Task 3) |
| §6.8 админка, возврат | Task 1 (строка карточки), Task 4 |
| §7 проверки владельца 1–5 | Task 2 (1–3), Task 1 (4–5) |
| §8 `/terms` | Task 3 |
| README, `.env.example` | Task 5 |
