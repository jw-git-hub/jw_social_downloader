"""Задача 2: статус-сообщение показывает план формата и проценты, сбой доски
не ломает загрузку/отправку, тикер не перезатирает финальный текст.

Каркас БД — как в tests/test_user_send_safety.py (`_make_session_maker`,
`_seed_user` из tests/test_user_handle_url.py). Свои копии двойников Message —
по тому же принципу, что в tests/test_silent_video_as_animation.py: файл
задачи строит дублёров сам, а не импортирует чужие приватные классы.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import bot.handlers.user as U
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage

from bot.config import settings
from bot.services.downloader import DownloadResult
from bot.services.progress_texts import PREPARING_TEXT, QUEUED_TEXT
from bot.services.upload_estimate import DEFAULT_UPLOAD_MIB_PER_SEC, UploadRateTracker
from bot.services.ytdlp_progress import DownloadPhase, DownloadStatus, FormatPlan
from tests.test_user_handle_url import TEST_URL, _free_downloads_left, _make_session_maker, _seed_user

_METHOD = SendMessage(chat_id=1, text="x")


async def _no_probe(path):
    return None


class _FakeStatusMessage:
    def __init__(self) -> None:
        self.edit_calls: list[str] = []
        self.delete_calls = 0

    async def edit_text(self, text, reply_markup=None):
        self.edit_calls.append(text)

    async def delete(self):
        self.delete_calls += 1


class _FakeMessage:
    """Минимальный двойник Message: reply/answer/reply_video, только то, что
    трогают `_process_download` и `_download_with_progress`."""

    def __init__(self, uid: int, *, on_reply_video=None) -> None:
        self.from_user = SimpleNamespace(id=uid, username="tester", full_name="Test User")
        self.chat = SimpleNamespace(id=uid)
        self.reply_calls: list[str] = []
        self.answer_calls: list[tuple[str, object]] = []
        self.reply_video_calls: list[dict] = []
        self.status_message: _FakeStatusMessage | None = None
        self._on_reply_video = on_reply_video

    async def reply(self, text, reply_markup=None):
        self.reply_calls.append(text)
        self.status_message = _FakeStatusMessage()
        return self.status_message

    async def answer(self, text, reply_markup=None):
        self.answer_calls.append((text, reply_markup))

    async def reply_video(self, video, caption=None, **kwargs):
        self.reply_video_calls.append(kwargs)
        if self._on_reply_video is not None:
            await self._on_reply_video()


# ── первый ответ — «Готовлю загрузку…» ──


async def test_first_reply_is_preparing_text(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "preparing.db")
    try:
        uid = 992001
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _ok(url, platform, on_status=None):
            return DownloadResult(success=True, file_path="/tmp/does-not-exist.mp4", media_type="video")

        monkeypatch.setattr(U, "download_media", _ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)

        msg = _FakeMessage(uid)
        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert msg.reply_calls == [PREPARING_TEXT]
    finally:
        await engine.dispose()


# ── сбой загрузки: тикер не перезатирает финальный текст ошибки ──


async def test_ticker_does_not_overwrite_failure_text(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "ticker_failure.db")
    try:
        uid = 992002
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        plan = FormatPlan(chosen_height=1080, chosen_mb=500.0, best_height=1080, best_mb=500.0)

        async def _fail_with_progress(url, platform, on_status=None):
            if on_status is not None:
                on_status(DownloadStatus(DownloadPhase.DOWNLOADING, plan, 0.5, 450.0, 60.0))
            return DownloadResult(success=False, error_message="boom")

        monkeypatch.setattr(U, "download_media", _fail_with_progress)

        msg = _FakeMessage(uid)
        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert msg.status_message is not None
        assert "Не удалось скачать" in msg.status_message.edit_calls[-1]
        calls_after_finish = len(msg.status_message.edit_calls)

        await asyncio.sleep(0.05)  # тикер, если бы выжил, успел бы тикнуть тут

        assert len(msg.status_message.edit_calls) == calls_after_finish
    finally:
        await engine.dispose()


# ── очередь: все слоты заняты ──


async def test_download_with_progress_shows_queued_text_when_semaphore_is_locked(monkeypatch):
    monkeypatch.setattr(U, "download_semaphore", asyncio.Semaphore(1))
    await U.download_semaphore.acquire()

    async def _ok(url, platform, on_status=None):
        return DownloadResult(success=True, file_path="/tmp/does-not-exist.mp4", media_type="video")

    monkeypatch.setattr(U, "download_media", _ok)

    status_msg = _FakeStatusMessage()
    board = U.StatusBoard(status_msg)

    task = asyncio.create_task(U._download_with_progress(board, TEST_URL, "tiktok"))
    await asyncio.sleep(0.01)  # дать циклу провернуться до захвата семафора

    assert status_msg.edit_calls
    assert status_msg.edit_calls[-1] == QUEUED_TEXT

    U.download_semaphore.release()
    dl_result, plan = await asyncio.wait_for(task, 1)

    assert dl_result.success is True
    assert plan is None


# ── запоминание скорости отправки ──


async def test_successful_send_records_upload_rate(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "record_rate.db")
    try:
        uid = 992003
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "upload_rate", UploadRateTracker())

        async def _ok(url, platform, on_status=None):
            return DownloadResult(
                success=True, file_path=str(video_path), media_type="video", file_size_mb=150.0
            )

        monkeypatch.setattr(U, "download_media", _ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)

        clock = {"now": 0.0}
        monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

        async def _advance_and_succeed():
            clock["now"] += 30.0

        msg = _FakeMessage(uid, on_reply_video=_advance_and_succeed)

        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert U.upload_rate.rate_mb_per_sec() != DEFAULT_UPLOAD_MIB_PER_SEC
        assert U.upload_rate.rate_mb_per_sec() == 150.0 / 30.0
    finally:
        await engine.dispose()


async def test_probably_delivered_does_not_record_upload_rate(monkeypatch, sqlite_engine_factory, tmp_path):
    """Тот же сценарий, что test_probably_delivered_is_not_resent_and_quota_is_kept
    (tests/test_user_send_safety.py), но с известным file_size_mb >= 50 —
    иначе `_wants_upload_progress` уже отсёк бы запись независимо от ветки
    probably_delivered, и тест ничего не доказывал бы про сам guard."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "no_record_probably.db")
    try:
        uid = 992004
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(U, "upload_rate", UploadRateTracker())
        monkeypatch.setattr(U, "SEND_RETRY_DELAYS", (0, 0, 0))
        monkeypatch.setattr(settings, "USE_LOCAL_BOT_API", True)

        async def _ok(url, platform, on_status=None):
            return DownloadResult(
                success=True, file_path=str(video_path), media_type="video", file_size_mb=150.0
            )

        monkeypatch.setattr(U, "download_media", _ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)

        clock = {"now": 0.0}
        monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

        async def _fail_after_long_wait():
            clock["now"] += 450  # >= PROBABLY_DELIVERED_AFTER_SEC
            raise TelegramNetworkError(method=_METHOD, message="idle timeout")

        msg = _FakeMessage(uid, on_reply_video=_fail_after_long_wait)

        await U._process_download(msg, [(TEST_URL, "tiktok")])

        assert await _free_downloads_left(maker, uid) == 0
        assert msg.status_message is not None
        assert "Telegram ещё обрабатывает" in msg.status_message.edit_calls[-1]
        assert U.upload_rate.rate_mb_per_sec() == DEFAULT_UPLOAD_MIB_PER_SEC
    finally:
        await engine.dispose()
