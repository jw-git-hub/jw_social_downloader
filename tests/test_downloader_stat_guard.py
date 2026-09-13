from pathlib import Path

from bot.services import downloader


def test_sized_files_drops_missing_and_empty(tmp_path):
    good = tmp_path / "good.mp4"
    good.write_bytes(b"x" * 10)
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    missing = tmp_path / "missing.mp4"

    result = downloader._sized_files([good, empty, missing])

    assert result == [(good, 10)]


def test_sized_files_does_not_raise_on_directory(tmp_path):
    subdir = tmp_path / "jw_cookies_abc"
    subdir.mkdir()
    # st_size у каталога ненулевой, но отправлять его нельзя — важно лишь,
    # что вызов не падает; отсев каталогов делает _find_downloaded_files.
    assert downloader._sized_files([subdir]) != []


async def test_gallery_fallback_returns_none_when_files_vanish(tmp_path, monkeypatch):
    """H-9: файл исчез между поиском и stat() — это не повод падать."""
    ghost = tmp_path / "deadbeef_1.jpg"

    async def fake_gallery_dl(url, filename):
        return [ghost]

    monkeypatch.setattr(downloader, "_try_gallery_dl", fake_gallery_dl)

    result = await downloader._try_gallery_dl_fallback("https://pin.it/abc", "deadbeef")

    assert result is None


async def test_download_media_reports_failure_when_files_vanish(tmp_path, monkeypatch):
    """H-9 на пути yt-dlp: вместо исключения — честный неуспех."""
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", tmp_path)
    ghost = tmp_path / "deadbeef_1.mp4"

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    def fake_find(directory, prefix):
        return [ghost]

    async def no_gallery(url, filename):
        return None

    monkeypatch.setattr(downloader.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(downloader, "_find_downloaded_files", fake_find)
    monkeypatch.setattr(downloader, "_try_gallery_dl_fallback", no_gallery)

    result = await downloader.download_media("https://www.youtube.com/watch?v=xyz", "youtube")

    assert result.success is False
    assert result.error_message
