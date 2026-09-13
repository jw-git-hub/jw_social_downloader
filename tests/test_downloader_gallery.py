from pathlib import Path

import pytest

from bot.services import downloader
from bot.services.downloader import (
    GALLERY_DL_MAX_ITEMS,
    _build_gallery_dl_cmd,
    _find_downloaded_files,
    _try_gallery_dl,
)
from tests._gallery_fake import write_fake_gallery_dl


# ── Командная строка ─────────────────────────────────────────────────────


def test_name_template_is_unique_per_item():
    cmd = _build_gallery_dl_cmd("https://www.pinterest.com/u/board/", "deadbeef", None)
    template = cmd[cmd.index("-f") + 1]
    assert template.startswith("deadbeef_")
    # {num} — порядок внутри поста, он должен идти первым, иначе сломается
    # ключ сортировки в _find_downloaded_files.
    assert template.index("{num}") < template.index("{filename")
    assert "{filename|num}" in template
    assert template.endswith(".{extension}")


def test_item_count_is_capped():
    cmd = _build_gallery_dl_cmd("https://www.pinterest.com/u/board/", "deadbeef", None)
    assert "--range" in cmd
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_MAX_ITEMS}"
    assert GALLERY_DL_MAX_ITEMS <= 10


def test_cookies_are_passed_only_when_present(tmp_path):
    assert "--cookies" not in _build_gallery_dl_cmd("https://pin.it/a", "abc", None)
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    cmd = _build_gallery_dl_cmd("https://pin.it/a", "abc", jar)
    assert cmd[cmd.index("--cookies") + 1] == str(jar)


def test_url_is_the_last_argument():
    cmd = _build_gallery_dl_cmd("https://pin.it/abc", "abc", None)
    assert cmd[-1] == "https://pin.it/abc"


# ── Сортировка при новом шаблоне ─────────────────────────────────────────


def test_sort_key_still_follows_carousel_order(tmp_path):
    prefix = "abc123"
    for name in (f"{prefix}_2_zzz.jpg", f"{prefix}_10_aaa.jpg", f"{prefix}_1_mmm.jpg"):
        (tmp_path / name).write_bytes(b"\xff\xd8\xffdata")
    found = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert found == [f"{prefix}_1_mmm.jpg", f"{prefix}_2_zzz.jpg", f"{prefix}_10_aaa.jpg"]


def test_board_items_share_num_and_sort_deterministically(tmp_path):
    prefix = "abc123"
    for name in (f"{prefix}_1_ccc.jpg", f"{prefix}_1_aaa.jpg", f"{prefix}_1_bbb.jpg"):
        (tmp_path / name).write_bytes(b"\xff\xd8\xffdata")
    found = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert found == [f"{prefix}_1_aaa.jpg", f"{prefix}_1_bbb.jpg", f"{prefix}_1_ccc.jpg"]


# ── Частичный успех и таймаут ────────────────────────────────────────────


@pytest.fixture
def fake_gallery_env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    return bin_dir, dl_dir


async def test_partial_failure_keeps_already_downloaded_files(fake_gallery_env):
    bin_dir, _dl_dir = fake_gallery_env
    write_fake_gallery_dl(
        bin_dir,
        files=3,
        exit_code=1,
        stderr_text="[pinterest][error] 2 items could not be downloaded\n",
    )
    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "abc123")
    assert run.returncode == 1
    assert len(run.files) == 3
    assert "could not be downloaded" in run.stderr


async def test_clean_exit_returns_files(fake_gallery_env):
    bin_dir, _dl_dir = fake_gallery_env
    write_fake_gallery_dl(bin_dir, files=2, exit_code=0)
    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "abc123")
    assert run.returncode == 0
    assert len(run.files) == 2


async def test_timeout_removes_partial_files(fake_gallery_env, monkeypatch):
    bin_dir, dl_dir = fake_gallery_env
    write_fake_gallery_dl(bin_dir, files=2, exit_code=0)
    monkeypatch.setattr(downloader.settings, "DOWNLOAD_TIMEOUT", 900)

    real_wait_for = downloader.asyncio.wait_for

    async def fake_wait_for(awaitable, timeout):
        # Дать поддельному gallery-dl реально дописать файлы, и только потом
        # изобразить таймаут — иначе проверять было бы нечего.
        await real_wait_for(awaitable, timeout)
        raise downloader.asyncio.TimeoutError

    monkeypatch.setattr(downloader.asyncio, "wait_for", fake_wait_for)

    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "abc123")
    assert run.files == []
    assert list(Path(dl_dir).glob("abc123*")) == []
