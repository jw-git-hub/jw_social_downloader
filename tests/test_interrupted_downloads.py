"""Тесты на bot.services.interrupted_downloads.

Сессии — через `_make_session_maker` из `tests/test_user_handle_url.py`
(готовая SQLite-схема прод-конфигурации), пользователей и брони сеем сами.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from bot.db.models import FreeDownload, PendingDownload, User
from bot.db.pending_downloads import add_pending
from bot.services.interrupted_downloads import notify_interrupted_downloads
from tests.test_user_handle_url import _make_session_maker


class _FakeBot:
    """Двойник Bot: `send_message` записывает вызов либо падает для чата."""

    def __init__(self, fail_for_chat_id: int | None = None) -> None:
        self.sent: list[tuple[int, str, dict]] = []
        self._fail_for_chat_id = fail_for_chat_id

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id == self._fail_for_chat_id:
            raise RuntimeError("simulated Telegram failure")
        self.sent.append((chat_id, text, kwargs))


async def _seed_user(maker, user_id: int) -> None:
    async with maker() as session, session.begin():
        session.add(User(id=user_id, username="tester", full_name="Test User"))


async def _add_free_download(maker, user_id: int) -> int:
    async with maker() as session, session.begin():
        session.add(FreeDownload(user_id=user_id, reserved_at=datetime.now(timezone.utc)))
        await session.flush()
        return await session.scalar(select(FreeDownload.id).where(FreeDownload.user_id == user_id))


async def _add_pending(maker, **kwargs) -> None:
    async with maker() as session, session.begin():
        await add_pending(session, **kwargs)


async def test_two_chats_one_refunded_one_not(sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "two_chats.db")
    try:
        await _seed_user(maker, 1)
        await _seed_user(maker, 2)
        reservation_id = await _add_free_download(maker, 1)

        # Чат A (пользователь 1, бесплатный): две ссылки, у одной бронь.
        await _add_pending(
            maker, user_id=1, chat_id=100, url="https://youtu.be/a", reservation_id=reservation_id
        )
        await _add_pending(
            maker, user_id=1, chat_id=100, url="https://youtu.be/b", reservation_id=None
        )
        # Чат B (пользователь 2, подписчик): без брони.
        await _add_pending(
            maker, user_id=2, chat_id=200, url="https://youtu.be/c", reservation_id=None
        )

        bot = _FakeBot()
        await notify_interrupted_downloads(bot, maker)

        by_chat = {chat_id: (text, kwargs) for chat_id, text, kwargs in bot.sent}
        assert set(by_chat) == {100, 200}

        text_a, kwargs_a = by_chat[100]
        assert "youtu.be/a" in text_a
        assert "youtu.be/b" in text_a
        assert "возвращены" in text_a

        text_b, kwargs_b = by_chat[200]
        assert "youtu.be/c" in text_b
        assert "возвращены" not in text_b

        for _, kwargs in by_chat.values():
            assert kwargs["link_preview_options"].is_disabled is True
    finally:
        await engine.dispose()


async def test_old_record_is_refunded_and_removed_without_a_message(sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "old_record.db")
    try:
        await _seed_user(maker, 3)
        reservation_id = await _add_free_download(maker, 3)
        old_created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await _add_pending(
            maker,
            user_id=3,
            chat_id=300,
            url="https://youtu.be/old",
            reservation_id=reservation_id,
            now=old_created_at,
        )

        bot = _FakeBot()
        await notify_interrupted_downloads(bot, maker)

        assert bot.sent == []
        async with maker() as session:
            remaining = await session.scalar(
                select(FreeDownload).where(FreeDownload.id == reservation_id)
            )
            assert remaining is None
            pending_rows = (await session.scalars(select(PendingDownload))).all()
            assert pending_rows == []
    finally:
        await engine.dispose()


async def test_send_message_failure_for_one_chat_does_not_block_another(sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "fail_chat.db")
    try:
        await _seed_user(maker, 4)
        await _add_pending(
            maker, user_id=4, chat_id=400, url="https://youtu.be/a", reservation_id=None
        )
        await _add_pending(
            maker, user_id=4, chat_id=401, url="https://youtu.be/b", reservation_id=None
        )

        bot = _FakeBot(fail_for_chat_id=400)
        await notify_interrupted_downloads(bot, maker)

        assert [chat_id for chat_id, _, _ in bot.sent] == [401]
    finally:
        await engine.dispose()


async def test_session_factory_failure_is_swallowed_and_nothing_is_sent():
    def _broken_factory():
        raise RuntimeError("db is down")

    bot = _FakeBot()

    await notify_interrupted_downloads(bot, _broken_factory)  # не должно бросить

    assert bot.sent == []
