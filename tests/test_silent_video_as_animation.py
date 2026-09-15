"""Беззвучное короткое видео (Pinterest-«гифка») должно уходить пользователю
через reply_animation, а не reply_video — Telegram-«гифка» это и есть mp4 без
звука, отправленный sendAnimation'ом.

Только одиночный путь отправки (`else:` в _process_download, media_type ==
"video"); карусель (media group) этот тест не трогает — правку туда сознательно
не вносили: там reply_animation вынес бы файл из альбома.

Каркас — как в tests/test_user_media.py (_FakeMediaMessage) + связка с sqlite
из tests/test_user_handle_url.py (_make_session_maker/_seed_user).
"""

from __future__ import annotations

from types import SimpleNamespace

from bot.handlers.user import _media_caption, _process_download
from bot.services.downloader import DownloadResult
from bot.services.media_probe import MediaInfo
from tests.test_user_handle_url import TEST_URL, _make_session_maker, _seed_user


class _FakeStatusMessage:
    """Двойник сообщения-статуса, которое возвращает message.reply()."""

    async def edit_text(self, text, reply_markup=None):
        pass

    async def delete(self):
        pass


class _FakeMediaMessage:
    """Минимальный двойник Message: только то, что трогает блок отправки
    _process_download. Своя копия (не импорт из test_user_media.py) — по
    заданию границ этой задачи дублёры строятся в этом же файле."""

    def __init__(self, uid: int) -> None:
        self.from_user = SimpleNamespace(id=uid, username="tester", full_name="Test User")
        self.reply_calls: list[str] = []
        self.answer_calls: list[tuple[str, object]] = []
        self.animation_calls: list[tuple[str, str | None]] = []
        self.photo_calls: list[tuple[str, str | None]] = []
        self.video_calls: list[tuple[str, str | None]] = []
        self.document_calls: list[tuple[str, str | None]] = []

    async def reply(self, text, reply_markup=None):
        self.reply_calls.append(text)
        return _FakeStatusMessage()

    async def answer(self, text, reply_markup=None):
        self.answer_calls.append((text, reply_markup))

    async def reply_animation(self, animation, caption=None):
        self.animation_calls.append((str(animation.path), caption))

    async def reply_photo(self, photo, caption=None):
        self.photo_calls.append((str(photo.path), caption))

    async def reply_video(self, video, caption=None):
        self.video_calls.append((str(video.path), caption))

    async def reply_document(self, document, caption=None):
        self.document_calls.append((str(document.path), caption))


def _silent_info(duration: float) -> MediaInfo:
    return MediaInfo(has_video=True, has_audio=False, duration=duration, width=640, height=640)


def _voiced_info(duration: float = 5.0) -> MediaInfo:
    return MediaInfo(has_video=True, has_audio=True, duration=duration, width=640, height=640)


async def test_silent_short_video_is_sent_as_animation(monkeypatch, sqlite_engine_factory, tmp_path):
    """Немое видео 5с -> reply_animation с подписью "animation", reply_video не звался."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "silent_short.db")
    try:
        uid = 990201
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True, file_path="/tmp/does-not-exist-silent.mp4", media_type="video"
            )

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        async def _probe(path):
            return _silent_info(5.0)

        monkeypatch.setattr("bot.handlers.user.probe_media", _probe)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "pinterest")

        assert msg.animation_calls == [
            ("/tmp/does-not-exist-silent.mp4", _media_caption("pinterest", "animation"))
        ]
        assert msg.video_calls == []
    finally:
        await engine.dispose()


async def test_video_with_audio_is_sent_as_video(monkeypatch, sqlite_engine_factory, tmp_path):
    """Видео со звуком -> reply_video, reply_animation не звался."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "with_audio.db")
    try:
        uid = 990202
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True, file_path="/tmp/does-not-exist-audio.mp4", media_type="video"
            )

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        async def _probe(path):
            return _voiced_info()

        monkeypatch.setattr("bot.handlers.user.probe_media", _probe)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "tiktok")

        assert msg.video_calls == [
            ("/tmp/does-not-exist-audio.mp4", _media_caption("tiktok", "video"))
        ]
        assert msg.animation_calls == []
    finally:
        await engine.dispose()


async def test_silent_video_over_duration_cap_is_sent_as_video(monkeypatch, sqlite_engine_factory, tmp_path):
    """Немое, но 120с (> SILENT_VIDEO_AS_ANIMATION_MAX_SEC=60) -> reply_video."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "silent_long.db")
    try:
        uid = 990203
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True, file_path="/tmp/does-not-exist-long.mp4", media_type="video"
            )

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        async def _probe(path):
            return _silent_info(120.0)

        monkeypatch.setattr("bot.handlers.user.probe_media", _probe)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "pinterest")

        assert msg.video_calls == [
            ("/tmp/does-not-exist-long.mp4", _media_caption("pinterest", "video"))
        ]
        assert msg.animation_calls == []
    finally:
        await engine.dispose()


async def test_unreadable_file_probe_none_falls_back_to_video(monkeypatch, sqlite_engine_factory, tmp_path):
    """probe_media вернул None (нечитаемый файл) -> reply_video, без исключения."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "probe_none.db")
    try:
        uid = 990204
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True, file_path="/tmp/does-not-exist-unreadable.mp4", media_type="video"
            )

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        async def _probe(path):
            return None

        monkeypatch.setattr("bot.handlers.user.probe_media", _probe)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "pinterest")  # не должно бросить

        assert msg.video_calls == [
            ("/tmp/does-not-exist-unreadable.mp4", _media_caption("pinterest", "video"))
        ]
        assert msg.animation_calls == []
    finally:
        await engine.dispose()


async def test_probe_media_raising_falls_back_to_video_without_crashing(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    """probe_media бросил исключение -> reply_video, хендлер не падает.

    Реализация оборачивает вызов probe_media в try/except внутри
    _process_download (а не полагается на то, что сам probe_media никогда не
    бросает) — этот тест целится именно в этот guard, подсовывая мок, который
    бросает исключение при вызове."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "probe_raises.db")
    try:
        uid = 990205
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True, file_path="/tmp/does-not-exist-crash.mp4", media_type="video"
            )

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        async def _probe(path):
            raise RuntimeError("ffprobe boom")

        monkeypatch.setattr("bot.handlers.user.probe_media", _probe)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "pinterest")  # не должно бросить

        assert msg.video_calls == [
            ("/tmp/does-not-exist-crash.mp4", _media_caption("pinterest", "video"))
        ]
        assert msg.animation_calls == []
    finally:
        await engine.dispose()


async def test_silent_short_webm_is_sent_as_video(monkeypatch, sqlite_engine_factory, tmp_path):
    """Немое короткое видео, но контейнер .webm (не .mp4) -> reply_video,
    reply_animation не звался: sendAnimation в Telegram документирован только
    для GIF и H.264/MPEG-4 AVC без звука, не-mp4 контейнер туда лучше не пускать."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "silent_webm.db")
    try:
        uid = 990207
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True, file_path="/tmp/does-not-exist-silent.webm", media_type="video"
            )

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        async def _probe(path):
            return _silent_info(5.0)

        monkeypatch.setattr("bot.handlers.user.probe_media", _probe)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "pinterest")

        assert msg.video_calls == [
            ("/tmp/does-not-exist-silent.webm", _media_caption("pinterest", "video"))
        ]
        assert msg.animation_calls == []
    finally:
        await engine.dispose()


async def test_image_media_type_never_calls_probe_media(monkeypatch, sqlite_engine_factory, tmp_path):
    """media_type == "image" -> probe_media вообще не зовётся, поведение прежнее."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "image_no_probe.db")
    try:
        uid = 990206
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True, file_path="/tmp/does-not-exist.jpg", media_type="image"
            )

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        async def _must_not_be_called(path):
            raise AssertionError("probe_media must not be called for media_type == 'image'")

        monkeypatch.setattr("bot.handlers.user.probe_media", _must_not_be_called)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "pinterest")

        assert msg.photo_calls == [
            ("/tmp/does-not-exist.jpg", _media_caption("pinterest", "image"))
        ]
        assert msg.animation_calls == []
        assert msg.video_calls == []
    finally:
        await engine.dispose()
