"""Задача 2: статус-сообщение показывает план формата и проценты, сбой доски
не ломает загрузку/отправку, тикер не перезатирает финальный текст.

Каркас БД — как в tests/test_user_send_safety.py (`_make_session_maker`,
`_seed_user` из tests/test_user_handle_url.py). Свои копии двойников Message —
по тому же принципу, что в tests/test_silent_video_as_animation.py: файл
задачи строит дублёров сам, а не импортирует чужие приватные классы.
"""

from __future__ import annotations

import asyncio
import gc
from types import SimpleNamespace

import bot.handlers.user as U
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from loguru import logger

import bot.services.chat_action as chat_action_module
from bot.config import settings
from bot.services.downloader import DownloadResult
from bot.services.progress_texts import PREPARING_TEXT, QUEUED_TEXT
from bot.services.upload_estimate import DEFAULT_UPLOAD_MIB_PER_SEC, UploadRateTracker
from bot.services.ytdlp_progress import DownloadPhase, DownloadStatus, FormatPlan
from tests.fake_bot import FakeBot
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
        self.bot = FakeBot()
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


# ── счётчик «идёт N с» без прогресса ──


async def test_download_without_on_status_shows_elapsed_counter(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    async def _slow_gallery_dl(url, platform, on_status=None):
        clock["now"] += 15.0  # gallery-dl on_status не зовёт
        await asyncio.sleep(0.05)  # тикер доски успевает отрисовать
        return DownloadResult(success=True, file_path="/tmp/does-not-exist.jpg", media_type="image")

    monkeypatch.setattr(U, "download_media", _slow_gallery_dl)

    status_msg = _FakeStatusMessage()
    board = U.StatusBoard(status_msg, interval=0.01)
    await U._download_with_progress(board, TEST_URL, "pinterest")

    assert any("Готовлю загрузку… · идёт 15 с" in text for text in status_msg.edit_calls)


def test_upload_renderer_small_file_shows_elapsed_counter(monkeypatch):
    clock = {"now": 10.0}
    monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    dl_result = DownloadResult(success=True, file_path="/x.mp4", media_type="video", file_size_mb=2.0)

    render = U._upload_renderer(dl_result, None)
    clock["now"] += 15.0

    assert render() == "<b>Отправляю в Telegram · 2 МБ · идёт 15 с</b>"


def test_upload_renderer_large_video_keeps_progress_bar(monkeypatch):
    dl_result = DownloadResult(success=True, file_path="/x.mp4", media_type="video", file_size_mb=150.0)

    assert "идёт" not in U._upload_renderer(dl_result, None)()


# ── chat action на время отправки ──


def test_chat_action_follows_media_kind():
    video = DownloadResult(success=True, file_path="/x.mp4", media_type="video")
    image = DownloadResult(success=True, file_path="/x.jpg", media_type="image")
    animation = DownloadResult(success=True, file_path="/x.gif", media_type="animation")

    assert U._chat_action_name(video) == ChatAction.UPLOAD_VIDEO
    assert U._chat_action_name(image) == ChatAction.UPLOAD_PHOTO
    assert U._chat_action_name(animation) == ChatAction.UPLOAD_DOCUMENT


def test_chat_action_for_album_follows_first_item():
    album = DownloadResult(success=True, file_paths=["/a.jpg", "/b.mp4"], media_type="image")
    video_first = DownloadResult(success=True, file_paths=["/a.mp4", "/b.jpg"], media_type="video")

    assert U._chat_action_name(album) == ChatAction.UPLOAD_PHOTO
    assert U._chat_action_name(video_first) == ChatAction.UPLOAD_VIDEO


async def _run_send(
    monkeypatch, sqlite_engine_factory, tmp_path, name, uid, on_reply_video=None, bot=None
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, name)
    try:
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"x" * 1024)
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)
        monkeypatch.setattr(chat_action_module, "CHAT_ACTION_INTERVAL_SEC", 0.01)

        async def _ok(url, platform, on_status=None):
            return DownloadResult(success=True, file_path=str(video_path), media_type="video")

        monkeypatch.setattr(U, "download_media", _ok)
        monkeypatch.setattr(U, "probe_media", _no_probe)

        msg = _FakeMessage(uid, on_reply_video=on_reply_video)
        if bot is not None:
            msg.bot = bot
        await U._process_download(msg, [(TEST_URL, "tiktok")])
        return msg
    finally:
        await engine.dispose()


async def _assert_chat_action_stopped(bot) -> None:
    sent_before = len(bot.chat_actions)
    await asyncio.sleep(0.1)  # при интервале 0,01 с живой цикл прислал бы ещё несколько
    assert len(bot.chat_actions) == sent_before


async def test_send_runs_under_upload_video_chat_action(monkeypatch, sqlite_engine_factory, tmp_path):
    async def _let_worker_send():
        await asyncio.sleep(0.05)

    msg = await _run_send(
        monkeypatch, sqlite_engine_factory, tmp_path, "chat_action_ok.db", 992010, _let_worker_send
    )

    assert msg.bot.chat_actions[0] == ChatAction.UPLOAD_VIDEO
    assert msg.bot.last_request_timeout == chat_action_module.CHAT_ACTION_TIMEOUT_SEC
    await _assert_chat_action_stopped(msg.bot)


async def test_chat_action_stops_when_send_fails(monkeypatch, sqlite_engine_factory, tmp_path):
    async def _boom():
        await asyncio.sleep(0.05)
        raise RuntimeError("telegram down")

    msg = await _run_send(monkeypatch, sqlite_engine_factory, tmp_path, "chat_action_fail.db", 992011, _boom)

    await _assert_chat_action_stopped(msg.bot)


class _BrokenChatActionBot(FakeBot):
    async def send_chat_action(self, chat_id, action, message_thread_id=None, request_timeout=None):
        await super().send_chat_action(chat_id, action, message_thread_id, request_timeout)
        raise TelegramNetworkError(method=_METHOD, message="chat action failed")


class _HangingChatActionBot(FakeBot):
    async def send_chat_action(self, chat_id, action, message_thread_id=None, request_timeout=None):
        await super().send_chat_action(chat_id, action, message_thread_id, request_timeout)
        await asyncio.Event().wait()


async def test_failing_chat_action_does_not_break_send_or_leak_exception(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    unhandled: list[dict] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: unhandled.append(context))
    delivered: list[bool] = []

    async def _delivered_while_actions_fail():
        await asyncio.sleep(0.05)  # цикл успевает несколько раз упасть
        delivered.append(True)

    bot = _BrokenChatActionBot()
    msg = await _run_send(
        monkeypatch, sqlite_engine_factory, tmp_path, "chat_action_broken.db", 992012,
        _delivered_while_actions_fail, bot,
    )
    gc.collect()
    await asyncio.sleep(0)

    assert delivered == [True]
    assert len(msg.bot.chat_actions) >= 2  # цикл пережил первую же ошибку
    assert unhandled == []
    await _assert_chat_action_stopped(msg.bot)


async def test_hanging_chat_action_does_not_delay_finish(monkeypatch, sqlite_engine_factory, tmp_path):
    async def _let_worker_hang():
        await asyncio.sleep(0.05)

    bot = _HangingChatActionBot()
    msg = await asyncio.wait_for(
        _run_send(
            monkeypatch, sqlite_engine_factory, tmp_path, "chat_action_hang.db", 992013,
            _let_worker_hang, bot,
        ),
        timeout=5,
    )

    assert len(msg.bot.chat_actions) == 1  # запрос завис, но отправка не ждала его
    assert not (tmp_path / "video.mp4").exists()  # уборка файла после отправки выполнена


# ── диагностика скорости выгрузки ──


def _capture_logs(level: str = "INFO"):
    records: list[tuple[str, str]] = []
    sink_id = logger.add(lambda m: records.append((m.record["level"].name, m.record["message"])), level=level)
    return records, sink_id


async def _send_taking(monkeypatch, seconds: float, size_mb: float | None):
    clock = {"now": 0.0}
    monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    async def _send():
        clock["now"] += seconds
        return "sent"

    return await U._send_with_retry(_send, size_mb)


async def test_send_with_retry_logs_size_and_speed(monkeypatch):
    records, sink_id = _capture_logs()
    try:
        await _send_taking(monkeypatch, seconds=10.0, size_mb=20.0)
    finally:
        logger.remove(sink_id)

    done = [msg for level, msg in records if "Отправка в Telegram завершена" in msg]
    assert len(done) == 1
    assert "size=20.0 МБ" in done[0]
    assert "speed=2.00 МБ/с" in done[0]
    assert not any(level == "WARNING" for level, _ in records)


async def test_send_with_retry_warns_about_slow_upload(monkeypatch):
    records, sink_id = _capture_logs()
    try:
        await _send_taking(monkeypatch, seconds=100.0, size_mb=10.0)  # 0,1 МБ/с
    finally:
        logger.remove(sink_id)

    warnings = [msg for level, msg in records if level == "WARNING"]
    assert len(warnings) == 1
    assert "Медленная выгрузка в Telegram" in warnings[0]
    assert "0.10 МБ/с" in warnings[0]


async def test_send_with_retry_does_not_warn_for_tiny_file(monkeypatch):
    records, sink_id = _capture_logs()
    try:
        await _send_taking(monkeypatch, seconds=100.0, size_mb=0.5)
    finally:
        logger.remove(sink_id)

    assert not any(level == "WARNING" for level, _ in records)


async def test_send_with_retry_without_size_logs_without_speed(monkeypatch):
    records, sink_id = _capture_logs()
    try:
        await _send_taking(monkeypatch, seconds=100.0, size_mb=None)
    finally:
        logger.remove(sink_id)

    done = [msg for level, msg in records if "Отправка в Telegram завершена" in msg]
    assert len(done) == 1
    assert "speed" not in done[0]
    assert not any(level == "WARNING" for level, _ in records)


def test_files_size_mb_sums_album_and_skips_missing(tmp_path):
    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    first.write_bytes(b"x" * 1024 * 1024)
    second.write_bytes(b"x" * 1024 * 1024)

    total = U._files_size_mb([str(first), str(second), str(tmp_path / "missing.jpg")])

    assert total == 2.0
