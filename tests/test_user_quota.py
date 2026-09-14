from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.config import settings
from bot.db.models import User
from bot.db.queries import refund_free_download, reserve_free_download
from bot.handlers.user import _quota_action, _reserve_quota


def _tg_user(uid: int = 555001, username: str = "quota_tester", full_name: str = "Quota Tester"):
    # Хендлер обращается ровно к трём атрибутам Telegram-пользователя,
    # поэтому полноценная pydantic-модель aiogram здесь не нужна.
    return SimpleNamespace(id=uid, username=username, full_name=full_name)


# ── политика возврата единицы ──


def test_quota_action_keeps_when_nothing_was_reserved():
    # У подписчика и у забаненного резервирования не было — возвращать нечего.
    assert _quota_action(False, download_ok=False, media_sent_count=0) == "keep"
    assert _quota_action(False, download_ok=True, media_sent_count=0) == "keep"


def test_quota_action_refunds_when_download_failed():
    assert _quota_action(True, download_ok=False, media_sent_count=0) == "refund"


def test_quota_action_refunds_when_nothing_was_delivered():
    assert _quota_action(True, download_ok=True, media_sent_count=0) == "refund"


def test_quota_action_keeps_when_at_least_one_file_delivered():
    # Частично доставленный альбом не возвращаем: медиа у юзера уже есть.
    assert _quota_action(True, download_ok=True, media_sent_count=1) == "keep"
    assert _quota_action(True, download_ok=True, media_sent_count=5) == "keep"


# ── резервирование в одной транзакции с чтением ──


async def test_reserve_quota_takes_one_unit_from_new_user(db_session):
    uid, banned, has_sub, reserved = await _reserve_quota(db_session, _tg_user())

    assert (banned, has_sub, reserved) == (False, False, True)
    db_session.expire_all()
    user = await db_session.get(User, uid)
    assert user.free_downloads_left == settings.FREE_DOWNLOADS - 1


async def test_reserve_quota_is_atomic_for_the_last_unit(db_session):
    tg = _tg_user()
    db_session.add(
        User(id=tg.id, username=tg.username, full_name=tg.full_name, free_downloads_left=1)
    )
    await db_session.flush()

    first = (await _reserve_quota(db_session, tg))[3]
    second = (await _reserve_quota(db_session, tg))[3]

    # Второй заход обязан получить False, а не увести остаток в минус.
    assert (first, second) == (True, False)
    db_session.expire_all()
    assert (await db_session.get(User, tg.id)).free_downloads_left == 0


async def test_reserve_quota_does_not_touch_banned_user(db_session):
    tg = _tg_user()
    db_session.add(
        User(
            id=tg.id,
            username=tg.username,
            full_name=tg.full_name,
            free_downloads_left=3,
            is_banned=True,
        )
    )
    await db_session.flush()

    uid, banned, has_sub, reserved = await _reserve_quota(db_session, tg)

    assert banned is True
    assert reserved is False
    db_session.expire_all()
    assert (await db_session.get(User, tg.id)).free_downloads_left == 3


async def test_reserve_quota_skips_subscriber(db_session):
    # Fix round 1, п.1: остаток НЕНУЛЕВОЙ (3, не 0). С нулевым остатком
    # reserve_free_download сам вернул бы False из-за пустой квоты — это
    # маскирует полную потерю проверки has_subscription в _reserve_quota
    # (ревьюер эмпирически подтвердил: `if is_banned or has_subscription`
    # можно заменить на `if is_banned:` и все тесты этого файла останутся
    # зелёными, если остаток тут 0). Явная проверка "остаток не тронут"
    # ниже — это и есть то свойство, которое нужно закрыть.
    tg = _tg_user()
    # subscription_until хранится naive и трактуется слоем БД как UTC.
    until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5)
    db_session.add(
        User(
            id=tg.id,
            username=tg.username,
            full_name=tg.full_name,
            free_downloads_left=3,
            subscription_until=until,
        )
    )
    await db_session.flush()

    uid, banned, has_sub, reserved = await _reserve_quota(db_session, tg)

    assert has_sub is True
    assert reserved is False
    db_session.expire_all()
    assert (await db_session.get(User, tg.id)).free_downloads_left == 3


async def test_refund_returns_exactly_one_unit(db_session):
    # Контракт пакета C, на который опирается хендлер: возврат отдаёт ровно
    # единицу и не «чинит» остаток до максимума.
    tg = _tg_user()
    db_session.add(
        User(id=tg.id, username=tg.username, full_name=tg.full_name, free_downloads_left=3)
    )
    await db_session.flush()

    assert await reserve_free_download(db_session, tg.id) is True
    await refund_free_download(db_session, tg.id)

    db_session.expire_all()
    assert (await db_session.get(User, tg.id)).free_downloads_left == 3
