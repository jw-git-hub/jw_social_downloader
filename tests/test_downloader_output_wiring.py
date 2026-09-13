"""Проверка, что stderr обеих утилит реально доходит до пользователя, а не
теряется по дороге через аккумулятор `outputs` в `download_media`.

Ревьюер фикс-раунда 1 предъявил два убийственных мутанта над Task 11 —
оба давали `161 passed` без единого красного теста:
  - удалить `outputs.append(("gallery-dl", run.stderr))` в
    `_try_gallery_dl_fallback`;
  - удалить любой из двух `outputs.append(("yt-dlp", full_output))` в
    `download_media`.

`test_downloader_errors.py` собирает `outputs` руками и не гоняет
`download_media`; `test_downloader_gallery.py` не трогает yt-dlp вообще.
Здесь — сквозной прогон с поддельными бинарниками ОБЕИХ утилит, проверяющий
итоговый `DownloadResult.error_message`.
"""

from __future__ import annotations

import sys

import pytest

from bot.services import downloader
from tests._gallery_fake import write_fake_gallery_dl


def _write_fake_ytdlp(bin_dir, *, stderr_text: str, exit_code: int = 1) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "yt-dlp"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.stderr.write({stderr_text!r})\n"
        f"sys.exit({exit_code})\n"
    )
    script.chmod(0o755)


@pytest.fixture
def fake_tools_env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    return bin_dir, dl_dir


# instagram + не /p/, чтобы download_media пошёл штатным путём yt-dlp→gallery-dl
# fallback, а не gallery-first: обе утилиты реально запускаются по очереди.
_URL = "https://www.instagram.com/reel/abc123/"


async def test_gallery_dl_stderr_reaches_the_user_message(fake_tools_env):
    """Мутант «удалить outputs.append(("gallery-dl", ...))» красит этот тест."""
    bin_dir, _dl_dir = fake_tools_env
    _write_fake_ytdlp(bin_dir, stderr_text="ERROR: [instagram] generic ytdlp failure\n")
    write_fake_gallery_dl(
        bin_dir, files=0, exit_code=1,
        stderr_text="[instagram][error] MARKER_GALLERY_STDERR_REACHED_USER\n",
    )

    result = await downloader.download_media(_URL, "instagram")

    assert result.success is False
    assert "MARKER_GALLERY_STDERR_REACHED_USER" in result.error_message


async def test_ytdlp_stderr_reaches_the_user_message_when_gallery_dl_is_silent(fake_tools_env):
    """Мутант «удалить любой outputs.append(("yt-dlp", ...))» красит этот тест."""
    bin_dir, _dl_dir = fake_tools_env
    _write_fake_ytdlp(bin_dir, stderr_text="ERROR: MARKER_YTDLP_STDERR_REACHED_USER\n")
    write_fake_gallery_dl(bin_dir, files=0, exit_code=1, stderr_text="")

    result = await downloader.download_media(_URL, "instagram")

    assert result.success is False
    assert "MARKER_YTDLP_STDERR_REACHED_USER" in result.error_message
