from types import SimpleNamespace

from aiogram.types import InputMediaPhoto

from bot.handlers.user import (
    MAX_CONCURRENT_PER_USER,
    WAITING_TTL,
    _classify,
    _is_waiting,
    _mark_waiting,
    _media_caption,
    _process_download,
    _release_user_slot,
    _try_take_user_slot,
    user_active_downloads,
    waiting_for_url,
)
from bot.services.downloader import DownloadResult
from tests.test_user_handle_url import TEST_URL, _make_session_maker, _seed_user


# ── классификация файлов ──


def test_gif_is_classified_as_animation():
    assert _classify("/tmp/jw_downloads/abc_1.gif") == "animation"
    assert _classify("/tmp/jw_downloads/abc_1.GIF") == "animation"


def test_still_images_stay_images():
    for name in ("a.jpg", "a.jpeg", "a.png", "a.webp", "a.heic"):
        assert _classify(f"/tmp/jw_downloads/{name}") == "image"


def test_everything_else_is_video():
    assert _classify("/tmp/jw_downloads/a.mp4") == "video"
    assert _classify("/tmp/jw_downloads/a.mkv") == "video"
    assert _classify("/tmp/jw_downloads/noextension") == "video"


# ── per-user лимит слотов ──


def test_second_parallel_download_of_same_user_is_refused():
    uid = 990001
    try:
        assert MAX_CONCURRENT_PER_USER == 1
        assert _try_take_user_slot(uid) is True
        assert _try_take_user_slot(uid) is False
        _release_user_slot(uid)
        assert _try_take_user_slot(uid) is True
    finally:
        user_active_downloads.pop(uid, None)


def test_other_users_are_not_blocked():
    a, b = 990002, 990003
    try:
        assert _try_take_user_slot(a) is True
        assert _try_take_user_slot(b) is True
    finally:
        user_active_downloads.pop(a, None)
        user_active_downloads.pop(b, None)


def test_released_slot_leaves_no_garbage():
    uid = 990004
    _try_take_user_slot(uid)
    _release_user_slot(uid)
    assert uid not in user_active_downloads


# ── гигиена множества ожидающих ссылку ──


def test_waiting_entry_expires_by_age(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "bot.handlers.user.time", SimpleNamespace(monotonic=lambda: clock["now"])
    )
    waiting_for_url.clear()
    uid = 990005

    _mark_waiting(uid)
    assert _is_waiting(uid) is True

    clock["now"] += WAITING_TTL + 1
    assert _is_waiting(uid) is False
    assert uid not in waiting_for_url


def test_stale_entries_are_evicted_on_new_marks(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(
        "bot.handlers.user.time", SimpleNamespace(monotonic=lambda: clock["now"])
    )
    waiting_for_url.clear()

    _mark_waiting(990006)
    clock["now"] = WAITING_TTL + 10
    _mark_waiting(990007)

    assert 990006 not in waiting_for_url
    assert 990007 in waiting_for_url


# ── блок отправки: .gif уходит анимацией, а не статичным кадром ──


class _FakeStatusMessage:
    """Двойник сообщения-статуса, которое возвращает message.reply()."""

    async def edit_text(self, text, reply_markup=None):
        pass

    async def delete(self):
        pass


class _FakeMediaMessage:
    """Минимальный двойник Message: только то, что трогает блок отправки
    _process_download. Отдельный от _FakeMessage в test_user_handle_url.py —
    там нет reply_animation вовсе."""

    def __init__(self, uid: int) -> None:
        self.from_user = SimpleNamespace(id=uid, username="tester", full_name="Test User")
        self.reply_calls: list[str] = []
        self.answer_calls: list[tuple[str, object]] = []
        self.animation_calls: list[tuple[str, str | None]] = []
        self.photo_calls: list[tuple[str, str | None]] = []
        self.video_calls: list[tuple[str, str | None]] = []
        self.document_calls: list[tuple[str, str | None]] = []
        self.media_group_calls: list[list[tuple[str, str, str | None]]] = []

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

    async def reply_media_group(self, media):
        group = []
        for item in media:
            kind = "photo" if isinstance(item, InputMediaPhoto) else "video"
            group.append((kind, str(item.media.path), item.caption))
        self.media_group_calls.append(group)

    def all_captions(self) -> list[str | None]:
        caps = [c for _, c in self.animation_calls]
        caps += [c for _, c in self.photo_calls]
        caps += [c for _, c in self.video_calls]
        caps += [c for _, c in self.document_calls]
        for group in self.media_group_calls:
            caps += [c for _, _, c in group]
        return caps


async def test_carousel_sends_gif_standalone_and_captions_once(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    """Карусель [gif, mp4, mp4]: гифка уходит отдельным reply_animation, а не
    в media group вместе с видео; подпись ставится ровно одному элементу."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "carousel_gif.db")
    try:
        uid = 990101
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        file_paths = [
            "/tmp/does-not-exist-a.gif",
            "/tmp/does-not-exist-b.mp4",
            "/tmp/does-not-exist-c.mp4",
        ]

        async def _ok(url, platform):
            return DownloadResult(success=True, file_paths=file_paths, media_type="video")

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "tiktok")

        assert len(msg.animation_calls) == 1
        assert msg.animation_calls[0][0] == "/tmp/does-not-exist-a.gif"

        assert len(msg.media_group_calls) == 1
        group = msg.media_group_calls[0]
        assert {path for _, path, _ in group} == {
            "/tmp/does-not-exist-b.mp4",
            "/tmp/does-not-exist-c.mp4",
        }
        assert all(kind == "video" for kind, _, _ in group)

        non_empty_captions = [c for c in msg.all_captions() if c]
        assert len(non_empty_captions) == 1
    finally:
        await engine.dispose()


async def test_single_animation_uses_reply_animation_with_caption(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    """Одиночный файл с media_type=animation уходит через reply_animation
    (не reply_photo), с подписью _media_caption(platform, "animation")."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "single_gif.db")
    try:
        uid = 990102
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        async def _ok(url, platform):
            return DownloadResult(
                success=True,
                file_path="/tmp/does-not-exist-single.gif",
                media_type="animation",
            )

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "pinterest")

        assert msg.animation_calls == [
            ("/tmp/does-not-exist-single.gif", _media_caption("pinterest", "animation"))
        ]
        assert msg.photo_calls == []
        assert msg.video_calls == []
    finally:
        await engine.dispose()


async def test_carousel_all_gifs_skips_media_group(
    monkeypatch, sqlite_engine_factory, tmp_path
):
    """Чанк из одних гифок: sendable пуст, media group не отправляется вовсе,
    все файлы уходят через reply_animation с ровно одной подписью."""
    maker, engine = await _make_session_maker(sqlite_engine_factory, tmp_path, "all_gifs.db")
    try:
        uid = 990103
        await _seed_user(maker, id=uid, free_downloads_left=1)
        monkeypatch.setattr("bot.handlers.user.async_session", maker)

        file_paths = ["/tmp/does-not-exist-x.gif", "/tmp/does-not-exist-y.gif"]

        async def _ok(url, platform):
            return DownloadResult(success=True, file_paths=file_paths, media_type="video")

        monkeypatch.setattr("bot.handlers.user.download_media", _ok)

        msg = _FakeMediaMessage(uid)
        await _process_download(msg, TEST_URL, "pinterest")

        assert msg.media_group_calls == []
        assert len(msg.animation_calls) == 2
        assert {path for path, _ in msg.animation_calls} == set(file_paths)

        non_empty_captions = [c for c in msg.all_captions() if c]
        assert len(non_empty_captions) == 1
    finally:
        await engine.dispose()
