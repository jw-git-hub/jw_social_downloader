import sys
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
    from bot.services.downloader import GalleryDlRun

    ghost = tmp_path / "deadbeef_1.jpg"

    async def fake_gallery_dl(url, platform, filename):
        return GalleryDlRun(files=[ghost], stderr="", returncode=0)

    monkeypatch.setattr(downloader, "_try_gallery_dl", fake_gallery_dl)

    outputs: list[tuple[str, str]] = []
    result = await downloader._try_gallery_dl_fallback("https://pin.it/abc", "pinterest", "deadbeef", outputs)

    assert result is None


async def test_download_media_reports_failure_when_files_vanish(tmp_path, monkeypatch):
    """H-9 на пути yt-dlp: вместо исключения — честный неуспех через
    `_sized_files`, а не как попало через внешний `except Exception`.

    `success is False` и непустой `error_message` сами по себе НЕ отличают
    правильный путь (`_sized_files` тихо съедает `FileNotFoundError` на
    `.stat()`) от регрессии, где кто-то заменил `_sized_files` на голый
    `.stat()`: тогда исключение улетает во внешний `except Exception`
    `download_media`, и результат ВНЕШНЕ выглядит так же — просто получен
    другим путём (мутация проверена ревью фикс-раунда 1: `161 passed` даже
    без уточнения ниже — на gallery-dl-пути guard пришпилен по-настоящему,
    здесь же не был). Разные пути дают РАЗНУЮ форму сообщения — проверяем её.
    """
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

    async def no_gallery(url, platform, filename, outputs):
        return None

    monkeypatch.setattr(downloader.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(downloader, "_find_downloaded_files", fake_find)
    monkeypatch.setattr(downloader, "_try_gallery_dl_fallback", no_gallery)

    result = await downloader.download_media("https://www.youtube.com/watch?v=xyz", "youtube")

    assert result.success is False
    assert result.error_message
    # Путь через _sized_files: честная классификация "нет данных об ошибке".
    # Путь через голый .stat(): "Непредвиденная ошибка: [Errno 2] ...".
    assert result.error_message.startswith("❌ Ошибка загрузки")
    assert "Непредвиденная ошибка" not in result.error_message


async def test_ytdlp_timeout_kill_does_not_raise_when_process_already_exited(tmp_path, monkeypatch):
    """Та же гонка asyncio.wait_for, что и в gallery-dl-ветке (см.
    tests/test_downloader_gallery.py::test_timeout_removes_partial_files),
    только на пути yt-dlp (находка ревью фикс-раунда 1, Minor #9): без
    guard'а process.kill() на уже завершившемся процессе бросает
    ProcessLookupError, она уходит во внешний except Exception, и
    пользователь вместо честного «⏱ Таймаут» получает «Непредвиденная
    ошибка».
    """
    bin_dir = tmp_path / "bin"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    monkeypatch.setattr(downloader.settings, "DOWNLOAD_TIMEOUT", 900)

    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "yt-dlp"
    # Реальный процесс, который успевает полностью завершиться раньше, чем
    # fake_wait_for изобразит таймаут ниже — иначе гонку было бы не
    # воспроизвести.
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(0)\n")
    script.chmod(0o755)

    real_wait_for = downloader.asyncio.wait_for

    async def fake_wait_for(awaitable, timeout):
        await real_wait_for(awaitable, timeout)
        raise downloader.asyncio.TimeoutError

    monkeypatch.setattr(downloader.asyncio, "wait_for", fake_wait_for)

    result = await downloader.download_media("https://www.youtube.com/watch?v=xyz", "youtube")

    assert result.success is False
    assert "Таймаут" in result.error_message
    assert "Непредвиденная ошибка" not in result.error_message
