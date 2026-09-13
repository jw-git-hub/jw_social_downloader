from pathlib import Path

import pytest

from bot.services import downloader
from bot.services.downloader import (
    GALLERY_DL_CAROUSEL_MAX_ITEMS,
    GALLERY_DL_MAX_ITEMS,
    _build_gallery_dl_cmd,
    _find_downloaded_files,
    _try_gallery_dl,
)
from tests._gallery_fake import write_fake_gallery_dl


# ── Командная строка ─────────────────────────────────────────────────────


def test_name_template_is_unique_per_item():
    cmd = _build_gallery_dl_cmd("https://www.pinterest.com/u/board/", "pinterest", "deadbeef", None)
    template = cmd[cmd.index("-f") + 1]
    assert template.startswith("deadbeef_")
    # {num} — порядок внутри поста, он должен идти первым, иначе сломается
    # ключ сортировки в _find_downloaded_files.
    assert template.index("{num}") < template.index("{id")
    # {id}, а не {filename}: у Pinterest {filename} — хеш CDN-URL картинки,
    # у двух репинов одной картинки на одной доске он совпадает и второй
    # пропускается как «уже существует» (находка ревью фикс-раунда 1).
    assert "{id|filename|num}" in template
    assert template.endswith(".{extension}")


def test_board_link_gets_low_item_cap():
    """Доска — потенциально тысячи элементов (1723 в живом тесте)."""
    cmd = _build_gallery_dl_cmd("https://www.pinterest.com/u/board/", "pinterest", "deadbeef", None)
    assert "--range" in cmd
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_MAX_ITEMS}"
    assert GALLERY_DL_MAX_ITEMS <= 10


def test_profile_link_gets_low_item_cap():
    """Профиль целиком — тот же неограниченный класс, что и доска."""
    cmd = _build_gallery_dl_cmd("https://www.pinterest.com/someuser/", "pinterest", "deadbeef", None)
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_MAX_ITEMS}"


def test_search_link_gets_low_item_cap():
    cmd = _build_gallery_dl_cmd(
        "https://www.pinterest.com/search/pins/?q=cats", "pinterest", "deadbeef", None
    )
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_MAX_ITEMS}"


def test_single_pin_link_gets_higher_item_cap():
    """Регрессия фикс-раунда 1: доска и одиночный пин раньше получали один
    и тот же низкий потолок — карусель у одиночного пина при этом ограничена
    не была вовсе, коллизий имён там не было, а обрезаться она стала."""
    cmd = _build_gallery_dl_cmd(
        "https://www.pinterest.com/pin/1234567890/", "pinterest", "deadbeef", None
    )
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_CAROUSEL_MAX_ITEMS}"
    assert GALLERY_DL_CAROUSEL_MAX_ITEMS >= 20


def test_pinit_shortlink_gets_higher_item_cap():
    cmd = _build_gallery_dl_cmd("https://pin.it/abc123", "pinterest", "deadbeef", None)
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_CAROUSEL_MAX_ITEMS}"


def test_instagram_carousel_gets_higher_item_cap():
    """Карусель Instagram (/p/) нативно ограничена ~20 элементами платформой
    — тот же низкий потолок, что у доски, раньше молча обрезал её до 10."""
    cmd = _build_gallery_dl_cmd(
        "https://www.instagram.com/p/abc123/", "instagram", "deadbeef", None
    )
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_CAROUSEL_MAX_ITEMS}"


def test_tiktok_slideshow_gets_higher_item_cap():
    cmd = _build_gallery_dl_cmd(
        "https://www.tiktok.com/@user/photo/123", "tiktok", "deadbeef", None
    )
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_CAROUSEL_MAX_ITEMS}"


def test_cookies_are_passed_only_when_present(tmp_path):
    assert "--cookies" not in _build_gallery_dl_cmd("https://pin.it/a", "pinterest", "abc", None)
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    cmd = _build_gallery_dl_cmd("https://pin.it/a", "pinterest", "abc", jar)
    assert cmd[cmd.index("--cookies") + 1] == str(jar)


def test_url_is_the_last_argument():
    cmd = _build_gallery_dl_cmd("https://pin.it/abc", "pinterest", "abc", None)
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


def test_part_files_are_never_returned(tmp_path):
    """gallery-dl/yt-dlp пишут во временный `<...>.part`, пока элемент не
    докачан; при обрыве он может остаться на диске. Без фильтра он уходил
    пользователю как обычный файл (живая находка ревью фикс-раунда 1)."""
    prefix = "abc123"
    (tmp_path / f"{prefix}_1_aaa.jpg").write_bytes(b"\xff\xd8\xffdata")
    (tmp_path / f"{prefix}_2_bbb.jpg.part").write_bytes(b"partial-bytes")
    found = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert found == [f"{prefix}_1_aaa.jpg"]


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
    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "pinterest", "abc123")
    assert run.returncode == 1
    assert len(run.files) == 3
    assert "could not be downloaded" in run.stderr


async def test_clean_exit_returns_files(fake_gallery_env):
    bin_dir, _dl_dir = fake_gallery_env
    write_fake_gallery_dl(bin_dir, files=2, exit_code=0)
    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "pinterest", "abc123")
    assert run.returncode == 0
    assert len(run.files) == 2


# ── partial/truncated: контракт для пакета D (Important #5) ─────────────


async def test_partial_flag_set_when_some_items_failed(fake_gallery_env):
    bin_dir, _dl_dir = fake_gallery_env
    write_fake_gallery_dl(
        bin_dir, files=3, exit_code=1, stderr_text="[pinterest][error] 2 items failed\n"
    )
    outputs: list[tuple[str, str]] = []
    result = await downloader._try_gallery_dl_fallback(
        "https://www.pinterest.com/u/board/", "pinterest", "abc123", outputs
    )
    assert result is not None
    assert result.partial is True
    assert result.truncated is False  # 3 файла, потолок доски — 10


async def test_truncated_flag_set_when_result_hits_the_cap(fake_gallery_env, monkeypatch):
    monkeypatch.setattr(downloader, "GALLERY_DL_MAX_ITEMS", 3)
    bin_dir, _dl_dir = fake_gallery_env
    write_fake_gallery_dl(bin_dir, files=3, exit_code=0)
    outputs: list[tuple[str, str]] = []
    result = await downloader._try_gallery_dl_fallback(
        "https://www.pinterest.com/u/board/", "pinterest", "abc123", outputs
    )
    assert result is not None
    assert result.truncated is True
    assert result.partial is False  # чистый выход, ничего не падало


async def test_neither_flag_set_on_full_clean_result(fake_gallery_env):
    bin_dir, _dl_dir = fake_gallery_env
    write_fake_gallery_dl(bin_dir, files=2, exit_code=0)
    outputs: list[tuple[str, str]] = []
    result = await downloader._try_gallery_dl_fallback(
        "https://www.pinterest.com/u/board/", "pinterest", "abc123", outputs
    )
    assert result is not None
    assert result.partial is False
    assert result.truncated is False


async def test_timeout_removes_partial_files(fake_gallery_env, monkeypatch):
    bin_dir, dl_dir = fake_gallery_env
    write_fake_gallery_dl(bin_dir, files=2, exit_code=0)
    monkeypatch.setattr(downloader.settings, "DOWNLOAD_TIMEOUT", 900)

    real_wait_for = downloader.asyncio.wait_for
    # Предусловие (находка ревью фикс-раунда 1): если поддельный gallery-dl
    # по какой-то причине не запустился (например, сломанный шебанг — уже
    # бывало в этом же файле), `run.files` и так пуст, а каталог и так чист
    # — обе финальные проверки совпали бы с пустотой, ничего не доказав про
    # работу _cleanup_glob. Явно фиксируем, что файлы БЫЛИ на диске в
    # момент, когда timeout ещё не «случился».
    files_before_timeout: list[list[Path]] = []

    async def fake_wait_for(awaitable, timeout):
        # Дать поддельному gallery-dl реально дописать файлы, и только потом
        # изобразить таймаут — иначе проверять было бы нечего.
        await real_wait_for(awaitable, timeout)
        files_before_timeout.append(list(Path(dl_dir).glob("abc123*")))
        raise downloader.asyncio.TimeoutError

    monkeypatch.setattr(downloader.asyncio, "wait_for", fake_wait_for)

    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "pinterest", "abc123")

    assert len(files_before_timeout) == 1 and len(files_before_timeout[0]) == 2
    assert run.files == []
    assert list(Path(dl_dir).glob("abc123*")) == []
