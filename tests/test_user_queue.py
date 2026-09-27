"""Задача 2 очереди: `handle_url`/`_process_download` ставят ссылки одного
пользователя в `DownloadQueue` и качают их по одной, соблюдая квоту и лимит
`MAX_LINKS_PER_USER`.

Каркас БД — `_make_session_maker`/`_seed_user`/`_free_downloads_left` из
tests/test_user_handle_url.py (читаем — не редактируем). Очередь и доска
статуса подменяются свежими на каждый тест: общий модульный `download_queue`
не должен переживать между тестами, а быстрый тикер (`interval=0.01`) даёт
проверить обновление номера очереди за разумное время сна.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import bot.handlers.user as U
from sqlalchemy import select

from bot.db.models import PendingDownload
from bot.services.download_queue import DownloadQueue
from bot.services.downloader import DownloadResult
from bot.services.progress_texts import PREPARING_TEXT
from bot.services.queue_texts import queue_status_text
from bot.services.status_board import StatusBoard
from tests.test_user_handle_url import _free_downloads_left, _make_session_maker, _seed_user

# Тесты ждут не фиксированным `sleep`, а опросом `_wait_until` — контейнер
# CI может быть заметно медленнее разработческой машины, и фиксированная
# пауза либо flaky, либо неоправданно длинная. WAIT_TIMEOUT — потолок
# ожидания, POLL_INTERVAL — шаг опроса.
WAIT_TIMEOUT = 2.0
POLL_INTERVAL = 0.01


async def _wait_until(predicate, timeout: float = WAIT_TIMEOUT) -> None:
    """Опрашивает `predicate` (обычный бул или корутина-фабрика) до истины.

    Бросает `AssertionError` с понятным сообщением, если условие не
    выполнилось за `timeout` секунд — иначе тест завис бы молча до общего
    таймаута pytest.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if loop.time() > deadline:
            raise AssertionError(f"условие не выполнилось за {timeout}с")
        await asyncio.sleep(POLL_INTERVAL)


async def _no_probe(path):
    return None


class _FakeStatusMessage:
    """Двойник сообщения-статуса, которое возвращает message.reply()."""

    def __init__(self) -> None:
        self.edit_calls: list[str] = []
        self.delete_calls = 0

    async def edit_text(self, text, reply_markup=None):
        self.edit_calls.append(text)

    async def delete(self):
        self.delete_calls += 1


class _FakeMessage:
    """Минимальный двойник Message: то, что трогают `handle_url` и очередь.

    `reply_raise_on_call` — номер по счёту вызова `reply()` (с 1), на
    котором нужно бросить исключение, имитируя сбой отправки статуса.
    """

    def __init__(self, uid: int, text: str) -> None:
        self.from_user = SimpleNamespace(id=uid, username="tester", full_name="Test User")
        self.chat = SimpleNamespace(id=uid)
        self.text = text
        self.caption = None
        self.reply_calls: list[tuple[str, dict]] = []
        self.status_messages: list[_FakeStatusMessage] = []
        self.answer_calls: list[tuple[str, object]] = []
        self.reply_video_calls: list[dict] = []
        self.reply_photo_calls: list[dict] = []
        self.reply_document_calls: list[dict] = []
        self.reply_animation_calls: list[dict] = []
        self.reply_media_group_calls: list[list] = []
        self.reply_raise_on_call: int | None = None
        self._reply_call_count = 0

    async def reply(self, text, reply_markup=None, **kwargs):
        self._reply_call_count += 1
        if self.reply_raise_on_call == self._reply_call_count:
            raise RuntimeError("simulated status send failure")
        status = _FakeStatusMessage()
        self.reply_calls.append((text, kwargs))
        self.status_messages.append(status)
        return status

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answer_calls.append((text, reply_markup))

    async def reply_video(self, video, caption=None, **kwargs):
        self.reply_video_calls.append(kwargs)

    async def reply_photo(self, photo, caption=None, **kwargs):
        self.reply_photo_calls.append(kwargs)

    async def reply_document(self, document, caption=None, **kwargs):
        self.reply_document_calls.append(kwargs)

    async def reply_animation(self, animation, caption=None, **kwargs):
        self.reply_animation_calls.append(kwargs)

    async def reply_media_group(self, media, **kwargs):
        self.reply_media_group_calls.append(media)

    def media_calls_total(self) -> int:
        return (
            len(self.reply_video_calls)
            + len(self.reply_photo_calls)
            + len(self.reply_document_calls)
            + len(self.reply_animation_calls)
            + sum(len(group) for group in self.reply_media_group_calls)
        )


class _FakeDownloader:
    """Двойник `download_media`: ждёт своё `asyncio.Event` на каждый url и
    считает, сколько загрузок идёт одновременно. `results` позволяет
    подставить свой `DownloadResult` (например, `success=False`) для
    конкретного url."""

    def __init__(self) -> None:
        self._gates: dict[str, asyncio.Event] = {}
        self.calls: list[str] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.results: dict[str, DownloadResult] = {}

    def gate_for(self, url: str) -> asyncio.Event:
        return self._gates.setdefault(url, asyncio.Event())

    async def __call__(self, url: str, platform: str, on_status=None) -> DownloadResult:
        self.calls.append(url)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await self.gate_for(url).wait()
        finally:
            self.concurrent -= 1
        return self.results.get(
            url,
            DownloadResult(success=True, file_path=f"/tmp/qtest-{abs(hash(url))}.mp4", media_type="video"),
        )


def _install_queue(monkeypatch) -> DownloadQueue:
    """Свежая очередь и быстрый тикер на каждый тест — иначе тесты делили бы
    один и тот же модульный `download_queue` между собой."""
    queue = DownloadQueue()
    monkeypatch.setattr(U, "download_queue", queue)
    monkeypatch.setattr(U, "StatusBoard", functools.partial(StatusBoard, interval=0.01))
    monkeypatch.setattr(U, "probe_media", _no_probe)
    return queue


async def _pending_rows(maker) -> list[PendingDownload]:
    async with maker() as session:
        return list((await session.scalars(select(PendingDownload))).all())


def _status_texts(msg: _FakeMessage) -> list[str]:
    """Тексты статус-сообщений (без отказа — у него в kwargs
    `link_preview_options`, у статуса кварги пустые)."""
    return [text for text, kwargs in msg.reply_calls if "link_preview_options" not in kwargs]


def _refusal_replies(msg: _FakeMessage) -> list[tuple[str, dict]]:
    return [(text, kwargs) for text, kwargs in msg.reply_calls if "link_preview_options" in kwargs]


# ── 1. Три ссылки одним сообщением ──


async def test_three_links_one_message_download_one_at_a_time(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "three_links.db")
    try:
        uid = 994001
        await _seed_user(maker, id=uid, free_left=3)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = [f"https://youtu.be/link{i}" for i in range(3)]
        msg = _FakeMessage(uid, " ".join(urls))

        task = asyncio.create_task(U.handle_url(msg))
        await _wait_until(lambda: downloader.calls == [urls[0]])
        downloader.gate_for(urls[0]).set()

        await _wait_until(lambda: downloader.calls == urls[:2])
        downloader.gate_for(urls[1]).set()

        await _wait_until(lambda: downloader.calls == urls)
        downloader.gate_for(urls[2]).set()

        await asyncio.wait_for(task, WAIT_TIMEOUT)

        assert _status_texts(msg) == [
            PREPARING_TEXT, queue_status_text(1), queue_status_text(2)
        ]
        assert downloader.max_concurrent == 1
        assert msg.media_calls_total() == 3
        assert await _free_downloads_left(maker, uid) == 0
        done_replies = [t for t, _ in msg.answer_calls if "Готово" in t]
        assert len(done_replies) == 1
    finally:
        await engine.dispose()


# ── 2. Два сообщения подряд ──


async def test_second_message_link_waits_for_the_first_to_finish(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "two_messages.db")
    try:
        uid = 994002
        await _seed_user(maker, id=uid, free_left=3)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        url1, url2 = "https://youtu.be/msg1", "https://youtu.be/msg2"
        msg1 = _FakeMessage(uid, url1)
        msg2 = _FakeMessage(uid, url2)

        task1 = asyncio.create_task(U.handle_url(msg1))
        await _wait_until(lambda: downloader.calls == [url1])
        task2 = asyncio.create_task(U.handle_url(msg2))

        # Вторая ссылка не должна начать качаться, пока не отпущена первая —
        # даём событийному циклу заметно провернуться и убеждаемся, что
        # список вызовов не подрос.
        await asyncio.sleep(0.1)
        assert downloader.calls == [url1]
        downloader.gate_for(url1).set()

        await _wait_until(lambda: downloader.calls == [url1, url2])
        downloader.gate_for(url2).set()

        await asyncio.wait_for(asyncio.gather(task1, task2), WAIT_TIMEOUT)
    finally:
        await engine.dispose()


# ── 3. Номер в очереди убывает ──


async def test_waiting_position_decreases_as_the_head_finishes(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "position.db")
    try:
        uid = 994003
        await _seed_user(maker, id=uid, free_left=3)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = [f"https://youtu.be/pos{i}" for i in range(3)]
        msg = _FakeMessage(uid, " ".join(urls))

        task = asyncio.create_task(U.handle_url(msg))
        await _wait_until(lambda: len(msg.status_messages) == 3)

        third_status = msg.status_messages[2]
        assert third_status.edit_calls == []

        downloader.gate_for(urls[0]).set()
        # Спецификация: тикер (интервал 0.01с в этом тесте) должен успеть
        # поправить статус в пределах ~0.3с.
        await _wait_until(lambda: queue_status_text(1) in third_status.edit_calls, timeout=0.3)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        await engine.dispose()


# ── 4. Бесплатный с остатком 1 и тремя ссылками ──


async def test_free_user_with_one_left_gets_one_status_and_one_paywall(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "one_left.db")
    try:
        uid = 994004
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = [f"https://youtu.be/free{i}" for i in range(3)]
        downloader.gate_for(urls[0]).set()
        msg = _FakeMessage(uid, " ".join(urls))

        await U.handle_url(msg)

        assert _status_texts(msg) == [PREPARING_TEXT]
        # Пейволл за вторую ссылку — единственная причина отказа: третья
        # ссылка сообщения вообще не разбирается (Д3 плана). Первая при
        # этом единственная в очереди и потому доводит дело до "Готово" —
        # оба answer-сообщения законны, пейволл среди них ровно один.
        paywall_replies = [t for t, _ in msg.answer_calls if "закончились" in t]
        assert len(paywall_replies) == 1
        assert downloader.calls == [urls[0]]
        assert await _free_downloads_left(maker, uid) == 0
    finally:
        await engine.dispose()


# ── 4b. Пачка сообщений с исчерпанной квотой шлёт один пейволл ──


async def test_paywall_is_sent_once_for_a_burst_of_messages(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "burst_paywall.db")
    try:
        uid = 994013
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)
        clock = {"now": 1000.0}
        monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
        U.last_paywall_at.clear()

        urls = [f"https://youtu.be/burst{i}" for i in range(5)]
        messages: list[_FakeMessage] = []
        for url in urls:
            downloader.gate_for(url).set()
            msg = _FakeMessage(uid, url)
            messages.append(msg)
            await U.handle_url(msg)

        # Первое сообщение забирает последнюю бесплатную загрузку и качается;
        # остальные четыре бьются об исчерпанную квоту, но пейволл — только
        # один на всю пачку (регрессия: раньше был на каждое сообщение).
        paywall_replies = [
            text for msg in messages for text, _ in msg.answer_calls if "закончились" in text
        ]
        assert len(paywall_replies) == 1
        assert downloader.calls == [urls[0]]
        assert await _free_downloads_left(maker, uid) == 0
    finally:
        await engine.dispose()


async def test_paywall_repeats_after_the_window(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "paywall_window.db")
    try:
        uid = 994014
        await _seed_user(maker, id=uid, free_left=1)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)
        clock = {"now": 1000.0}
        monkeypatch.setattr(U, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
        U.last_paywall_at.clear()

        first_url = "https://youtu.be/window0"
        downloader.gate_for(first_url).set()
        await U.handle_url(_FakeMessage(uid, first_url))  # тратит последнюю бесплатную загрузку

        second_msg = _FakeMessage(uid, "https://youtu.be/window1")
        await U.handle_url(second_msg)
        first_paywalls = [t for t, _ in second_msg.answer_calls if "закончились" in t]
        assert len(first_paywalls) == 1

        clock["now"] += U.PAYWALL_REPEAT_WINDOW + 1
        third_msg = _FakeMessage(uid, "https://youtu.be/window2")
        await U.handle_url(third_msg)
        second_paywalls = [t for t, _ in third_msg.answer_calls if "закончились" in t]
        assert len(second_paywalls) == 1
    finally:
        await engine.dispose()


# ── 5. Подписчик присылает 7 ссылок ──


async def test_subscriber_sends_seven_links_five_queue_two_refused(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "seven_links.db")
    try:
        uid = 994005
        await _seed_user(
            maker, id=uid, free_left=0, subscription_until=datetime.now(timezone.utc) + timedelta(days=1)
        )
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = [f"https://youtu.be/sub{i}" for i in range(7)]
        for url in urls[:5]:
            downloader.gate_for(url).set()
        msg = _FakeMessage(uid, " ".join(urls))

        await U.handle_url(msg)

        assert _status_texts(msg) == [
            PREPARING_TEXT,
            queue_status_text(1),
            queue_status_text(2),
            queue_status_text(3),
            queue_status_text(4),
        ]
        refusals = _refusal_replies(msg)
        assert len(refusals) == 1
        text, kwargs = refusals[0]
        assert text.count("очередь заполнена") == 2
        assert kwargs["link_preview_options"].is_disabled is True
        assert sorted(downloader.calls) == sorted(urls[:5])
    finally:
        await engine.dispose()


# ── 6. Дубликаты ──


async def test_duplicate_link_twice_in_one_message_is_refused_once(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "dup_one_message.db")
    try:
        uid = 994006
        await _seed_user(maker, id=uid, free_left=3)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        url = "https://youtu.be/dup"
        downloader.gate_for(url).set()
        msg = _FakeMessage(uid, f"{url} {url}")

        await U.handle_url(msg)

        assert downloader.calls == [url]
        refusals = _refusal_replies(msg)
        assert len(refusals) == 1
        assert "уже в очереди" in refusals[0][0]
    finally:
        await engine.dispose()


async def test_duplicate_link_in_second_message_while_downloading_is_refused(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "dup_second_message.db")
    try:
        uid = 994007
        await _seed_user(maker, id=uid, free_left=3)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        url = "https://youtu.be/dup2"
        msg1 = _FakeMessage(uid, url)
        msg2 = _FakeMessage(uid, url)

        task1 = asyncio.create_task(U.handle_url(msg1))
        await _wait_until(lambda: downloader.calls == [url])

        await U.handle_url(msg2)

        refusals = _refusal_replies(msg2)
        assert len(refusals) == 1
        assert "уже в очереди" in refusals[0][0]

        downloader.gate_for(url).set()
        await asyncio.wait_for(task1, WAIT_TIMEOUT)
    finally:
        await engine.dispose()


# ── 7. Одна из ссылок падает ──


async def test_second_link_failure_refunds_only_its_own_reservation(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "second_fails.db")
    try:
        uid = 994008
        await _seed_user(maker, id=uid, free_left=3)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = [f"https://youtu.be/fail{i}" for i in range(3)]
        downloader.results[urls[1]] = DownloadResult(success=False, error_message="boom")
        for url in urls:
            downloader.gate_for(url).set()
        msg = _FakeMessage(uid, " ".join(urls))

        await U.handle_url(msg)

        # 3 брони поставлены (по одной на ссылку), url1 проваливается и
        # возвращает свою — итог: 3 - 2 успешных = 1.
        assert await _free_downloads_left(maker, uid) == 1
    finally:
        await engine.dispose()


# ── 8. Статус второй ссылки не отправился ──


async def test_second_link_status_send_failure_refunds_and_skips_pending(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "status_fails.db")
    try:
        uid = 994009
        await _seed_user(maker, id=uid, free_left=3)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = [f"https://youtu.be/statusfail{i}" for i in range(3)]
        downloader.gate_for(urls[0]).set()
        msg = _FakeMessage(uid, " ".join(urls))
        msg.reply_raise_on_call = 2  # статус второй ссылки не уходит

        await U.handle_url(msg)

        # Первая скачана, третья вообще не поставлена (остановка на отказе).
        assert downloader.calls == [urls[0]]
        # Бронь второй возвращена: 3 - 1 (только первая потрачена) = 2.
        assert await _free_downloads_left(maker, uid) == 2
        assert await _pending_rows(maker) == []
    finally:
        await engine.dispose()


# ── 9. Журнал ──


async def test_pending_journal_is_empty_after_normal_completion(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "journal_clean.db")
    try:
        uid = 994010
        await _seed_user(maker, id=uid, free_left=2)
        monkeypatch.setattr(U, "async_session", maker)
        _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = ["https://youtu.be/journal0", "https://youtu.be/journal1"]
        for url in urls:
            downloader.gate_for(url).set()
        msg = _FakeMessage(uid, " ".join(urls))

        await U.handle_url(msg)

        assert await _pending_rows(maker) == []
    finally:
        await engine.dispose()


async def test_cancelling_handle_url_keeps_pending_rows_but_clears_the_line(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "journal_cancel.db")
    try:
        uid = 994011
        await _seed_user(maker, id=uid, free_left=2)
        monkeypatch.setattr(U, "async_session", maker)
        queue = _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = ["https://youtu.be/cancel0", "https://youtu.be/cancel1"]
        msg = _FakeMessage(uid, " ".join(urls))

        task = asyncio.create_task(U.handle_url(msg))
        # Обе ссылки уже поставлены (обе admit-транзакции синхронно отработали
        # в `_enqueue_links` до первого `_run_job`) ровно к моменту, когда
        # первая заблокировалась на своём gate — это и есть сигнал готовности.
        await _wait_until(lambda: downloader.calls == [urls[0]])

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert len(await _pending_rows(maker)) == 2
        assert queue.line_length(uid) == 0
    finally:
        await engine.dispose()


# ── 10. Неожиданное исключение в одном из заданий ──


async def test_unexpected_exception_in_one_job_does_not_stop_the_queue(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "unexpected.db")
    try:
        uid = 994012
        await _seed_user(maker, id=uid, free_left=2)
        monkeypatch.setattr(U, "async_session", maker)
        queue = _install_queue(monkeypatch)
        downloader = _FakeDownloader()
        monkeypatch.setattr(U, "download_media", downloader)

        urls = ["https://youtu.be/boom0", "https://youtu.be/boom1"]
        downloader.gate_for(urls[1]).set()

        original_execute = U._execute_download

        async def _boom_for_first(job):
            if job.url == urls[0]:
                raise RuntimeError("boom")
            await original_execute(job)

        monkeypatch.setattr(U, "_execute_download", _boom_for_first)

        msg = _FakeMessage(uid, " ".join(urls))
        await U.handle_url(msg)

        assert msg.status_messages[0].edit_calls[-1] == U.INTERNAL_ERROR_TEXT
        assert downloader.calls == [urls[1]]
        assert queue.line_length(uid) == 0
    finally:
        await engine.dispose()
