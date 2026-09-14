"""Fix round 1, п.2 и п.6: реальные тесты на handle_url.

До этого раунда правки `handle_url` (главный денежный механизм плана —
резервирование/возврат бесплатной квоты) не были покрыты НИ ОДНИМ тестом на
сам хендлер: ревьюер одной мутацией вырезал разом гейт бана, гейт пейволла и
ОБА вызова `_refund_quota`, и `342 passed` — CI ничего не заметил бы, удали
кто-то весь платёжный механизм целиком.

Каркас — предложение ревьюера, проверено им же на живом коде:
`_prod_like_sqlite_engine`-совместимый движок из `sqlite_engine_factory`
(fixture `tests/conftest.py`, читаем — не редактируем) +
`monkeypatch.setattr(U, "async_session", maker)` +
`monkeypatch.setattr(U, "download_media", ...)`. Полноценный мок Telegram не
нужен — двойника `aiogram.types.Message` заменяет плоский объект с
`.reply`/`.answer`/`.reply_video`/`.reply_photo`/`.reply_document`/
`.reply_media_group`/`.from_user`/`.text`.
"""

from __future__ import annotations

import bot.handlers.user as U
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import EXTRA_INDEX_DDL, Base, User
from bot.services.downloader import DownloadResult

# Реальная ссылка, которую parse_url распознаёт как TikTok — handle_url не
# должен уйти в ветку "это не похоже на ссылку" ни в одном сценарии.
TEST_URL = "https://www.tiktok.com/@someuser/video/1234567890123456789"


def _bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=None, message=text)


def _forbidden(text: str = "bot was blocked") -> TelegramForbiddenError:
    return TelegramForbiddenError(method=None, message=text)


class _FakeStatusMessage:
    """Двойник сообщения-статуса, которое возвращает message.reply()."""

    def __init__(self, edit_raises: Exception | None = None, delete_raises: Exception | None = None) -> None:
        self.edit_calls: list[str] = []
        self.delete_calls = 0
        self.edit_raises = edit_raises
        self.delete_raises = delete_raises

    async def edit_text(self, text, reply_markup=None):
        self.edit_calls.append(text)
        if self.edit_raises is not None:
            raise self.edit_raises

    async def delete(self):
        self.delete_calls += 1
        if self.delete_raises is not None:
            raise self.delete_raises


class _FakeUser:
    def __init__(self, uid: int, username: str = "tester", full_name: str = "Test User") -> None:
        self.id = uid
        self.username = username
        self.full_name = full_name


class _FakeMessage:
    """Минимальный двойник Message: только то, что реально трогает handle_url."""

    def __init__(
        self,
        text: str,
        uid: int,
        *,
        reply_raises: Exception | None = None,
        status_edit_raises: Exception | None = None,
        status_delete_raises: Exception | None = None,
    ) -> None:
        self.text = text
        self.from_user = _FakeUser(uid)
        self.reply_calls: list[str] = []
        self.answer_calls: list[tuple[str, object]] = []
        self.reply_raises = reply_raises
        self._status_edit_raises = status_edit_raises
        self._status_delete_raises = status_delete_raises
        self.status_message: _FakeStatusMessage | None = None
        self.reply_video_raises: Exception | None = None
        self.reply_photo_raises: Exception | None = None
        self.media_group_calls = 0
        self.media_group_raise_on_call: int | None = None
        self.sent_media: list[str] = []

    async def reply(self, text, reply_markup=None):
        self.reply_calls.append(text)
        if self.reply_raises is not None:
            raise self.reply_raises
        self.status_message = _FakeStatusMessage(
            edit_raises=self._status_edit_raises, delete_raises=self._status_delete_raises
        )
        return self.status_message

    async def answer(self, text, reply_markup=None):
        self.answer_calls.append((text, reply_markup))

    async def reply_video(self, video, caption=None):
        if self.reply_video_raises is not None:
            raise self.reply_video_raises
        self.sent_media.append("video")

    async def reply_photo(self, photo, caption=None):
        if self.reply_photo_raises is not None:
            raise self.reply_photo_raises
        self.sent_media.append("photo")

    async def reply_document(self, document, caption=None):
        self.sent_media.append("document")

    async def reply_media_group(self, media):
        self.media_group_calls += 1
        if self.media_group_raise_on_call == self.media_group_calls:
            raise RuntimeError("simulated media group send failure")
        self.sent_media.extend(["group"] * len(media))


async def _seed_user(maker, **overrides) -> None:
    fields = dict(
        id=800000001,
        username="tester",
        full_name="Test User",
        free_downloads_left=3,
        subscription_until=None,
        is_banned=False,
        total_downloads=0,
    )
    fields.update(overrides)
    async with maker() as session, session.begin():
        session.add(User(**fields))


async def _free_downloads_left(maker, user_id: int) -> int:
    async with maker() as session:
        return await session.scalar(select(User.free_downloads_left).where(User.id == user_id))


async def _make_session_maker(sqlite_engine_factory, tmp_path, name: str):
    from sqlalchemy import text

    engine = sqlite_engine_factory(tmp_path / name)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in EXTRA_INDEX_DDL:
            await conn.execute(text(statement))
    return async_sessionmaker(engine, expire_on_commit=False), engine


# ── п.2: главный денежный механизм handle_url ──


async def test_paywall_when_no_free_downloads_left(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "paywall.db")
    try:
        uid = 800000001
        await _seed_user(maker, id=uid, free_downloads_left=0)
        monkeypatch.setattr(U, "async_session", maker)

        async def _must_not_be_called(*a, **k):
            raise AssertionError("download_media must not be called when the paywall gate holds")

        monkeypatch.setattr(U, "download_media", _must_not_be_called)

        msg = _FakeMessage(TEST_URL, uid)
        await U.handle_url(msg)

        assert msg.answer_calls, "paywall must answer, not reply"
        assert "лимит" in msg.answer_calls[0][0].lower()
        assert await _free_downloads_left(maker, uid) == 0
    finally:
        await engine.dispose()


async def test_banned_user_is_not_reserved(monkeypatch, sqlite_engine_factory, tmp_path):
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "banned.db")
    try:
        uid = 800000002
        await _seed_user(maker, id=uid, free_downloads_left=3, is_banned=True)
        monkeypatch.setattr(U, "async_session", maker)

        async def _must_not_be_called(*a, **k):
            raise AssertionError("download_media must not be called for a banned user")

        monkeypatch.setattr(U, "download_media", _must_not_be_called)

        msg = _FakeMessage(TEST_URL, uid)
        await U.handle_url(msg)

        assert msg.reply_calls == ["🚫 Ваш аккаунт заблокирован. Обратитесь к администратору."]
        assert await _free_downloads_left(maker, uid) == 3
    finally:
        await engine.dispose()


async def test_refund_on_download_failure(monkeypatch, sqlite_engine_factory, tmp_path):
    """Заодно закрывает except (TelegramBadRequest, TelegramForbiddenError)
    вокруг status_msg.edit_text в ветке `if not dl_result.success`."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "dl_fail.db")
    try:
        uid = 800000003
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _fail(url, platform):
            return DownloadResult(success=False, error_message="boom")

        monkeypatch.setattr(U, "download_media", _fail)

        msg = _FakeMessage(TEST_URL, uid, status_edit_raises=_bad_request("can't parse entities"))
        await U.handle_url(msg)  # не должно бросить, несмотря на edit_text-исключение

        assert await _free_downloads_left(maker, uid) == 1
        assert msg.status_message is not None
        assert msg.status_message.edit_calls
    finally:
        await engine.dispose()


async def test_no_refund_on_partial_delivery(monkeypatch, sqlite_engine_factory, tmp_path):
    """Заодно закрывает except (TelegramBadRequest, TelegramForbiddenError)
    в финальной ветке ошибки отправки (после try/except/finally блока)."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "partial.db")
    try:
        uid = 800000004
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        # 7 файлов -> два чанка по MEDIA_GROUP_CHUNK_SIZE=5: 5 + 2. Первый
        # чанк уходит успешно (media_sent_count=5), второй падает.
        file_paths = [f"/tmp/does-not-exist-{i}.mp4" for i in range(7)]

        async def _ok(url, platform):
            return DownloadResult(success=True, file_paths=file_paths, media_type="video")

        monkeypatch.setattr(U, "download_media", _ok)

        msg = _FakeMessage(TEST_URL, uid, status_edit_raises=_bad_request("can't parse entities"))
        msg.media_group_raise_on_call = 2
        await U.handle_url(msg)  # не должно бросить

        assert msg.media_group_calls == 2
        # Частично доставлено (5 из 7) -> единица НЕ возвращается.
        assert await _free_downloads_left(maker, uid) == 0
    finally:
        await engine.dispose()


# ── п.3: три замеренных окна потери оплаченной единицы ──


async def test_refund_when_status_message_send_is_forbidden(monkeypatch, sqlite_engine_factory, tmp_path):
    """Fix round 1, окно 1/3: user.py — первый await message.reply(...)
    после резервирования не был накрыт ничем."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "forbidden_status.db")
    try:
        uid = 800000005
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _must_not_be_called(*a, **k):
            raise AssertionError("download_media must not run if the status message never sent")

        monkeypatch.setattr(U, "download_media", _must_not_be_called)

        msg = _FakeMessage(TEST_URL, uid, reply_raises=_forbidden())
        await U.handle_url(msg)  # не должно бросить

        assert await _free_downloads_left(maker, uid) == 1
    finally:
        await engine.dispose()


async def test_refund_when_status_message_send_hits_network_error(monkeypatch, sqlite_engine_factory, tmp_path):
    """Fix round 2, N1: (TelegramBadRequest, TelegramForbiddenError) вокруг
    message.reply(...) статус-сообщения было слишком узко —
    TelegramNetworkError/TelegramRetryAfter не менее (а RetryAfter даже
    более) вероятны на этом шаге, и оба пролетали мимо guard'а необработанными,
    унося единицу с собой. Тест — на TelegramRetryAfter (первый вызов API
    после того, как юзер прислал несколько ссылок подряд)."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "retry_after_status.db")
    try:
        uid = 800000011
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _must_not_be_called(*a, **k):
            raise AssertionError("download_media must not run if the status message never sent")

        monkeypatch.setattr(U, "download_media", _must_not_be_called)

        msg = _FakeMessage(
            TEST_URL,
            uid,
            reply_raises=TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=5),
        )
        await U.handle_url(msg)  # не должно бросить

        assert await _free_downloads_left(maker, uid) == 1
    finally:
        await engine.dispose()


async def test_refund_when_download_media_raises(monkeypatch, sqlite_engine_factory, tmp_path):
    """Fix round 1, окно 2/3: download_media НЕ гарантирует DownloadResult —
    mkdir/gallery-dl fallback/ephemeral cookies выполняются до её try.
    Замерено ревьюером на OSError(ENOSPC)."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "dl_raises.db")
    try:
        uid = 800000006
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _boom(url, platform):
            raise OSError("No space left on device")

        monkeypatch.setattr(U, "download_media", _boom)

        msg = _FakeMessage(TEST_URL, uid)
        await U.handle_url(msg)  # не должно бросить

        assert await _free_downloads_left(maker, uid) == 1
        assert msg.status_message is not None
        assert msg.status_message.edit_calls
    finally:
        await engine.dispose()


async def test_refund_survives_log_download_lock_error(monkeypatch, sqlite_engine_factory, tmp_path):
    """Fix round 1, окно 3/3: возврат стоял ПОСЛЕ log_download, чья
    транзакция ничем не защищена. Замерено на RuntimeError('database is
    locked') — штатном исходе SQLite под нагрузкой."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "log_locked.db")
    try:
        uid = 800000007
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _fail(url, platform):
            return DownloadResult(success=False, error_message="boom")

        monkeypatch.setattr(U, "download_media", _fail)

        async def _locked_log(*a, **k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(U, "log_download", _locked_log)

        msg = _FakeMessage(TEST_URL, uid)
        await U.handle_url(msg)  # не должно бросить, несмотря на падение лога

        assert await _free_downloads_left(maker, uid) == 1
    finally:
        await engine.dispose()


async def test_success_path_survives_log_download_lock_error(monkeypatch, sqlite_engine_factory, tmp_path):
    """Fix round 2, N2: третий (последний) незащищённый сайт log_download —
    на УСПЕШНОМ пути. Без guard'а его падение уходит необработанным ДО
    status_msg.delete()/message.answer(): медиа уже доставлено, единица уже
    списана (деньги не теряются), но пользователь не видит "Готово", а
    статус-сообщение "Скачиваю..." остаётся висеть навсегда."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "success_log_locked.db")
    try:
        uid = 800000012
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True,
                file_path="/tmp/does-not-exist.mp4",
                media_type="video",
                file_size_mb=1.0,
            )

        monkeypatch.setattr(U, "download_media", _ok)

        async def _locked_log(*a, **k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(U, "log_download", _locked_log)

        msg = _FakeMessage(TEST_URL, uid)
        await U.handle_url(msg)  # не должно бросить, несмотря на падение лога

        # Единица уже честно списана (успех, не про деньги) — этот тест
        # про то, что пользователь всё равно получает финальный ответ и
        # статус-сообщение не остаётся висеть навсегда.
        assert msg.status_message is not None
        assert msg.status_message.delete_calls == 1
        assert msg.answer_calls
    finally:
        await engine.dispose()


# ── п.6: остальные конверсии except и новая ветка Forbidden ──


async def test_success_path_survives_status_delete_forbidden(monkeypatch, sqlite_engine_factory, tmp_path):
    """Закрывает except (TelegramBadRequest, TelegramForbiddenError) вокруг
    status_msg.delete() в ветке успеха."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "delete_forbidden.db")
    try:
        uid = 800000008
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True,
                file_path="/tmp/does-not-exist.mp4",
                media_type="video",
                file_size_mb=1.0,
            )

        monkeypatch.setattr(U, "download_media", _ok)

        msg = _FakeMessage(TEST_URL, uid, status_delete_raises=_forbidden())
        await U.handle_url(msg)  # не должно бросить

        # Успех: единица потрачена и НЕ возвращается, финальный ответ ушёл.
        assert await _free_downloads_left(maker, uid) == 0
        assert msg.answer_calls
    finally:
        await engine.dispose()


async def test_forbidden_during_send_triggers_refund(monkeypatch, sqlite_engine_factory, tmp_path):
    """Закрывает новую ветку `except TelegramForbiddenError as e:` в блоке
    отправки (юзер заблокировал бота посреди аплоада).

    Важная оговорка (честно, а не спрятано): по одному только возврату
    единицы эта ветка НЕ отличима от общего `except Exception` — оба пути
    в итоге зовут `_quota_action(reserved, download_ok=True,
    media_sent_count=0)`, что при `reserved=True` всегда даёт "refund"
    независимо от того, какой except сработал. Подтверждено мутацией: если
    убрать выделенный `except TelegramForbiddenError` целиком (провалившись
    в общий `except Exception`), этот тест по возврату НЕ краснеет — именно
    поэтому проверка ниже целится в единственное реально отличимое свойство
    этой ветки: уровень и текст лога (`INFO` "заблокировал бота", а не
    `ERROR` "Failed to send file").
    """
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "forbidden_send.db")
    try:
        uid = 800000009
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr(U, "async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True,
                file_path="/tmp/does-not-exist.mp4",
                media_type="video",
                file_size_mb=1.0,
            )

        monkeypatch.setattr(U, "download_media", _ok)

        info_calls: list[str] = []
        error_calls: list[str] = []
        monkeypatch.setattr(U.logger, "info", lambda msg, *a, **k: info_calls.append(msg))
        monkeypatch.setattr(U.logger, "error", lambda msg, *a, **k: error_calls.append(msg))

        msg = _FakeMessage(TEST_URL, uid)
        msg.reply_video_raises = _forbidden()
        await U.handle_url(msg)  # не должно бросить

        # Ничего не доставлено -> единица возвращается (общее для обеих веток).
        assert await _free_downloads_left(maker, uid) == 1
        # Отличимое свойство именно этой ветки: штатный INFO-лог про блокировку,
        # а не ERROR "Failed to send file" из общего except Exception.
        assert any("заблокировал бота" in m for m in info_calls)
        assert not any("Failed to send file" in m for m in error_calls)
    finally:
        await engine.dispose()
