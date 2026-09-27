"""Задача 2: без дублей, без заведомо лишних отдач, без забитого диска.

Каркас — тот же, что в tests/test_user_media.py: `_process_download` дёргается
напрямую (не через handle_url), обвязка БД — `_make_session_maker`/`_seed_user`/
`_free_downloads_left` из tests/test_user_handle_url.py (читаем — не редактируем).
"""

from __future__ import annotations

from types import SimpleNamespace

import bot.handlers.user as U
from aiogram.exceptions import TelegramEntityTooLarge, TelegramNetworkError
from aiogram.methods import SendMessage

from bot.config import settings
from bot.services import disk_space, downloader
from bot.services.downloader import DownloadResult
from bot.services.media_probe import MediaInfo
from bot.services.progress_texts import PREPARING_TEXT
from tests.test_user_handle_url import TEST_URL, _free_downloads_left, _make_session_maker, _seed_user

_METHOD = SendMessage(chat_id=1, text="x")


async def _no_probe(path):
    return None


class _FakeStatusMessage:
    """Двойник сообщения-статуса. `events`, если передан, копит порядок
    вперемешку с reply_video (см. test_sending_status_is_shown_before_upload)."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.edit_calls: list[str] = []
        self.delete_calls = 0
        self._events = events

    async def edit_text(self, text, reply_markup=None):
        self.edit_calls.append(text)
        if self._events is not None:
            self._events.append(f"edit:{text}")

    async def delete(self):
        self.delete_calls += 1


class _FakeSendSafetyMessage:
    """Минимальный двойник Message для тестов безопасной отправки.

    `on_reply_video` — необязательный async-колбэк без аргументов: может
    двигать тестовые часы и/или бросать исключение, изображая сбой отправки.
    """

    def __init__(
        self, uid: int, *, on_reply_video=None, events: list[str] | None = None
    ) -> None:
        self.from_user = SimpleNamespace(id=uid, username="tester", full_name="Test User")
        self.chat = SimpleNamespace(id=uid)
        self.reply_calls: list[str] = []
        self.answer_calls: list[tuple[str, object]] = []
        self.reply_video_calls: list[dict] = []
        self.status_message: _FakeStatusMessage | None = None
        self._on_reply_video = on_reply_video
        self._events = events

    async def reply(self, text, reply_markup=None):
        self.reply_calls.append(text)
        self.status_message = _FakeStatusMessage(self._events)
        return self.status_message

    async def answer(self, text, reply_markup=None):
        self.answer_calls.append((text, reply_markup))

    async def reply_video(self, video, caption=None, **kwargs):
        self.reply_video_calls.append(kwargs)
        if self._events is not None:
            self._events.append("reply_video")
        if self._on_reply_video is not None:
            await self._on_reply_video()


async def _seeded_video_download(maker, uid: int, video_path, *, free_left: int = 1):
    await _seed_user(maker, id=uid, free_left=free_left)

    async def _ok(url, platform, **_kwargs):
        return DownloadResult(success=True, file_path=str(video_path), media_type="video")

    return _ok


async def test_probably_delivered_is_not_resent_and_quota_is_kept(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "probably_delivered.db")
    try:
        uid = 991001
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        ok = await _seeded_video_download(maker, uid, video_path)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "download_media", ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)
        monkeypatch.setattr(U, "SEND_RETRY_DELAYS", (0, 0, 0))
        monkeypatch.setattr(settings, "USE_LOCAL_BOT_API", True)

        clock = {"now": 0.0}
        monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

        calls = {"n": 0}

        async def _fail_after_long_wait():
            calls["n"] += 1
            clock["now"] += 450  # >= PROBABLY_DELIVERED_AFTER_SEC
            raise TelegramNetworkError(method=_METHOD, message="idle timeout")

        msg = _FakeSendSafetyMessage(uid, on_reply_video=_fail_after_long_wait)

        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert calls["n"] == 1
        assert await _free_downloads_left(maker, uid) == 0
        assert msg.status_message is not None
        assert "Telegram ещё обрабатывает" in msg.status_message.edit_calls[-1]
        assert not any("Готово" in text for text, _ in msg.answer_calls)
    finally:
        await engine.dispose()


async def test_long_network_failure_in_cloud_mode_refunds_quota(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    """Тот же обрыв на 450с, но в облаке (USE_LOCAL_BOT_API=False, дефолт теста):

    у облачного Bot API нет нашего IDLE_TIMEOUT, поэтому это обычный сетевой
    сбой, а не «вероятно доставлено» — ретраить бессмысленно (файл уже не
    придёт), но квоту нужно вернуть и не обещать пользователю доставку.
    """
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "cloud_long_failure.db")
    try:
        uid = 991008
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        ok = await _seeded_video_download(maker, uid, video_path)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "download_media", ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)
        monkeypatch.setattr(U, "SEND_RETRY_DELAYS", (0, 0, 0))

        clock = {"now": 0.0}
        monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

        calls = {"n": 0}

        async def _fail_after_long_wait():
            calls["n"] += 1
            clock["now"] += 450  # >= PROBABLY_DELIVERED_AFTER_SEC
            raise TelegramNetworkError(method=_METHOD, message="idle timeout")

        msg = _FakeSendSafetyMessage(uid, on_reply_video=_fail_after_long_wait)

        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert calls["n"] == 1
        assert await _free_downloads_left(maker, uid) == 1
        assert msg.status_message is not None
        assert "Telegram ещё обрабатывает" not in msg.status_message.edit_calls[-1]
        assert not any("Готово" in text for text, _ in msg.answer_calls)
    finally:
        await engine.dispose()


async def test_quick_network_error_is_retried_once_then_succeeds(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "retry_success.db")
    try:
        uid = 991002
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        ok = await _seeded_video_download(maker, uid, video_path)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "download_media", ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)
        monkeypatch.setattr(U, "SEND_RETRY_DELAYS", (0, 0, 0))

        clock = {"now": 0.0}
        monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

        calls = {"n": 0}

        async def _fail_once_then_ok():
            calls["n"] += 1
            if calls["n"] == 1:
                clock["now"] += 1  # короткий сбой, не «вероятно доставлено»
                raise TelegramNetworkError(method=_METHOD, message="temporary blip")

        msg = _FakeSendSafetyMessage(uid, on_reply_video=_fail_once_then_ok)

        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert calls["n"] == 2
        assert await _free_downloads_left(maker, uid) == 0
        assert any("Готово" in text for text, _ in msg.answer_calls)
    finally:
        await engine.dispose()


async def test_entity_too_large_is_not_retried(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "too_large.db")
    try:
        uid = 991003
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        ok = await _seeded_video_download(maker, uid, video_path)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "download_media", ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)
        monkeypatch.setattr(U, "SEND_RETRY_DELAYS", (0, 0, 0))

        calls = {"n": 0}

        async def _too_large():
            calls["n"] += 1
            raise TelegramEntityTooLarge(method=_METHOD, message="too big for telegram")

        msg = _FakeSendSafetyMessage(uid, on_reply_video=_too_large)

        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert calls["n"] == 1
        assert await _free_downloads_left(maker, uid) == 1
        assert msg.status_message is not None
        assert "слишком большой" in msg.status_message.edit_calls[-1]
    finally:
        await engine.dispose()


async def test_oversized_file_is_not_sent_and_quota_refunded(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "oversized.db")
    try:
        uid = 991004
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * (2 * 1024 * 1024))
        ok = await _seeded_video_download(maker, uid, video_path)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "download_media", ok)
        monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1)

        msg = _FakeSendSafetyMessage(uid)

        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert msg.reply_video_calls == []
        assert await _free_downloads_left(maker, uid) == 1
        assert msg.status_message is not None
        assert "до 1 МБ" in msg.status_message.edit_calls[-1]
        assert not video_path.exists()
    finally:
        await engine.dispose()


async def test_low_disk_at_start_refunds_and_skips_download(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    """Д4 плана очереди: место проверяется в момент старта загрузки, а не
    при постановке — статус-сообщение к этому моменту уже отправлено
    (PREPARING_TEXT), и отказ по месту его правит, а не шлёт новое."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "low_disk.db")
    try:
        uid = 991005
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(disk_space, "disk_usage", lambda path: SimpleNamespace(free=0))

        async def _must_not_be_called(*a, **k):
            raise AssertionError("download_media must not be called when disk space is low")

        monkeypatch.setattr(U, "download_media", _must_not_be_called)

        msg = _FakeSendSafetyMessage(uid)
        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert msg.reply_calls == [PREPARING_TEXT]
        assert msg.status_message is not None
        assert msg.status_message.edit_calls[-1] == U.LOW_DISK_TEXT
        assert await _free_downloads_left(maker, uid) == 1
    finally:
        await engine.dispose()


async def test_sending_status_is_shown_before_upload(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "status_order.db")
    try:
        uid = 991006
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        ok = await _seeded_video_download(maker, uid, video_path)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "download_media", ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)

        events: list[str] = []
        msg = _FakeSendSafetyMessage(uid, events=events)

        await U._process_download(msg, [(TEST_URL, "tiktok")])

        sending_events = [e for e in events if e.startswith("edit:") and "Отправляю в Telegram" in e]
        assert sending_events
        assert events.index(sending_events[0]) < events.index("reply_video")
    finally:
        await engine.dispose()


async def test_single_video_is_streamable_with_dimensions(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "streamable.db")
    try:
        uid = 991007
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        ok = await _seeded_video_download(maker, uid, video_path)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "download_media", ok)

        async def _probe(path):
            return MediaInfo(has_video=True, has_audio=True, duration=12.4, width=1920, height=1080)

        monkeypatch.setattr(U, "probe_media", _probe)

        msg = _FakeSendSafetyMessage(uid)
        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert len(msg.reply_video_calls) == 1
        kwargs = msg.reply_video_calls[0]
        assert kwargs["supports_streaming"] is True
        assert kwargs["width"] == 1920
        assert kwargs["height"] == 1080
        assert kwargs["duration"] == 12
    finally:
        await engine.dispose()


def test_timeout_message_uses_minutes_not_old_120_seconds(monkeypatch):
    monkeypatch.setattr(downloader.settings, "DOWNLOAD_TIMEOUT", 900)
    text = downloader._timeout_message()
    assert "15 мин" in text
    assert "120" not in text
