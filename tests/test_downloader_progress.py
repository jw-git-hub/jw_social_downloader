"""Сквозной прогон `download_media` с поддельным yt-dlp в PATH: колбэк
`on_status` должен получать снимки прогресса, служебные строки JW* не должны
попадать в текст ошибки, а поведение при таймауте/ошибках — не меняться.

Приём поддельного бинарника — из `tests/test_downloader_output_wiring.py`;
здесь скрипт дополнительно находит путь вывода после `-o` и умеет создавать
файл, печатать произвольные строки stdout и завершаться с заданным кодом.
"""

from __future__ import annotations

import sys

import pytest

from bot.services import downloader
from bot.services.downloader import _build_command, download_media
from bot.services.ytdlp_progress import YTDLP_PROGRESS_ARGS, DownloadPhase

PLAN_1080P = (
    'JWPLAN {"width": 1920, "height": 1080, "filesize_approx": 931982591}\t'
    '[{"width": 3840, "height": 2160, "vcodec": "vp9", "filesize": 7244611584}, '
    '{"width": 1920, "height": 1080, "vcodec": "vp9", "filesize": 873505486}, '
    '{"vcodec": "none", "filesize": 58477105}]'
)


def _write_fake_ytdlp(
    bin_dir,
    *,
    stdout_lines: list[str] = (),
    stderr_text: str = "",
    exit_code: int = 0,
    create_file: bool = False,
    file_bytes: int = 1024,
) -> None:
    """Поддельный yt-dlp: печатает заданные строки stdout/stderr, при
    `create_file=True` создаёт файл по пути из `-o` (буквальному — без
    `%(ext)s`, платформа теста всегда youtube)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "yt-dlp"
    lines_repr = repr(list(stdout_lines))
    script.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "argv = sys.argv[1:]\n"
        "output_path = pathlib.Path(argv[argv.index('-o') + 1])\n"
        f"for line in {lines_repr}:\n"
        "    print(line, flush=True)\n"
        f"sys.stderr.write({stderr_text!r})\n"
        f"if {create_file!r}:\n"
        "    output_path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"    output_path.write_bytes(b'x' * {file_bytes})\n"
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


_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


async def test_successful_download_reports_downloading_and_merging_phases(fake_tools_env):
    bin_dir, _dl_dir = fake_tools_env
    _write_fake_ytdlp(
        bin_dir,
        stdout_lines=[
            PLAN_1080P,
            "JWPROG|315|downloading|471463670|873505486|NA",
            "JWPROG|315|finished|873505486|873505486|NA",
            "JWPP|Merger|started",
            "JWPP|MoveFiles|started",
        ],
        create_file=True,
    )

    statuses = []
    result = await download_media(_URL, "youtube", on_status=statuses.append)

    assert result.success is True
    phases = {status.phase for status in statuses}
    assert DownloadPhase.DOWNLOADING in phases
    assert DownloadPhase.MERGING in phases
    downloading_statuses = [s for s in statuses if s.phase == DownloadPhase.DOWNLOADING]
    assert any(s.fraction is not None for s in downloading_statuses)
    assert statuses[-1].plan.chosen_height == 1080


async def test_stderr_error_reaches_user_and_jw_lines_are_stripped(fake_tools_env):
    bin_dir, _dl_dir = fake_tools_env
    _write_fake_ytdlp(
        bin_dir,
        stdout_lines=[PLAN_1080P, "JWPROG|315|downloading|500|1000|NA"],
        stderr_text="ERROR: MARKER_X\n",
        exit_code=1,
        create_file=False,
    )

    result = await download_media(_URL, "youtube")

    assert result.success is False
    assert "MARKER_X" in result.error_message
    assert "JWPLAN" not in result.error_message
    assert "JWPROG" not in result.error_message


async def test_stdout_only_error_never_leaks_jw_service_lines(fake_tools_env):
    """Риск #3 брифа: JWPLAN — это 7–10 КБ JSON списка форматов. Строится
    сообщение об ошибке из stdout (stderr пуст) — служебные строки должны
    быть уже вырезаны `communicate_streaming`/`DownloadTracker.feed`, а не
    просочиться в `<code>` пользователю."""
    bin_dir, _dl_dir = fake_tools_env
    _write_fake_ytdlp(
        bin_dir,
        stdout_lines=[PLAN_1080P, "JWPROG|315|downloading|500|1000|NA", "ERROR: MARKER_STDOUT_ONLY"],
        stderr_text="",
        exit_code=1,
        create_file=False,
    )

    result = await download_media(_URL, "youtube")

    assert result.success is False
    assert "MARKER_STDOUT_ONLY" in result.error_message
    assert "JWPLAN" not in result.error_message
    assert "JWPROG" not in result.error_message


async def test_oversize_error_detected_from_stdout_only(fake_tools_env):
    bin_dir, _dl_dir = fake_tools_env
    _write_fake_ytdlp(
        bin_dir,
        stdout_lines=[
            PLAN_1080P,
            "JWPROG|315|downloading|1|2|NA",
            "[download] File is larger than max-filesize (2 bytes > 1 bytes). Aborting.",
        ],
        stderr_text="",
        exit_code=0,
        create_file=False,
    )

    result = await download_media(_URL, "youtube")

    assert result.success is False
    assert result.error_message.startswith("Файл слишком большой")


async def test_on_status_exception_does_not_fail_the_download(fake_tools_env):
    bin_dir, _dl_dir = fake_tools_env
    _write_fake_ytdlp(bin_dir, stdout_lines=[PLAN_1080P], create_file=True)

    def boom(_status):
        raise RuntimeError("status renderer exploded")

    result = await download_media(_URL, "youtube", on_status=boom)

    assert result.success is True


@pytest.mark.parametrize("platform", ["instagram", "tiktok", "facebook", "pinterest", "youtube"])
def test_progress_args_are_a_contiguous_block_right_before_the_url(tmp_path, platform):
    url = "https://example.invalid/x"
    cmd = _build_command(url, platform, tmp_path / "out.%(ext)s", None)

    assert cmd[-1] == url
    block_start = len(cmd) - 1 - len(YTDLP_PROGRESS_ARGS)
    assert cmd[block_start:-1] == list(YTDLP_PROGRESS_ARGS)
