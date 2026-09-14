import pytest
from loguru import logger

from bot.services import downloader
from bot.services.downloader import _build_command, _ephemeral_cookies, _try_gallery_dl
from tests._gallery_fake import write_fake_gallery_dl

PLATFORMS = ("instagram", "tiktok", "pinterest", "facebook", "youtube")


# ── TLS и возрастной фильтр ──────────────────────────────────────────────


def test_tls_verification_is_never_disabled(tmp_path):
    for platform in PLATFORMS:
        cmd = _build_command(
            "https://example.invalid/x", platform, tmp_path / "o.%(ext)s", None
        )
        assert "--no-check-certificates" not in cmd, platform
        assert "--no-check-certificate" not in cmd, platform


def test_client_side_age_filter_is_not_imposed(tmp_path):
    for platform in PLATFORMS:
        cmd = _build_command(
            "https://example.invalid/x", platform, tmp_path / "o.%(ext)s", None
        )
        assert "--age-limit" not in cmd, platform


def test_cookies_flag_still_passed_when_jar_given(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    cmd = _build_command("https://example.invalid/x", "tiktok", tmp_path / "o.mp4", jar)
    assert cmd[cmd.index("--cookies") + 1] == str(jar)


# ── Копия кук в RAM-каталоге ─────────────────────────────────────────────


def test_cookie_copy_lands_inside_the_tmpfs_download_dir(tmp_path, monkeypatch):
    dl_dir = tmp_path / "downloads"
    master = tmp_path / "master-cookies.txt"
    original = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tsessionid\tSECRET\n"
    master.write_text(original)

    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    monkeypatch.setattr(downloader.settings, "COOKIES_FILE", str(master))

    with _ephemeral_cookies() as jar:
        assert jar is not None
        # Каталог кук — ВНУТРИ tmpfs-папки загрузок, а не в /tmp контейнера.
        assert jar.parent.parent == dl_dir
        assert jar.read_text() == original
        # Копия перезаписывается утилитами — имитируем это и проверяем,
        # что мастер-файл не пострадал.
        jar.write_text("# Netscape HTTP Cookie File\n")
        leaked = jar.parent

    # Каталог убран в finally.
    assert not leaked.exists()
    # Мастер-файл цел: ради этого эфемерные копии и заведены.
    assert master.read_text() == original


def test_no_cookie_file_yields_none(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(downloader.settings, "COOKIES_FILE", "")
    with _ephemeral_cookies() as jar:
        assert jar is None


# ── Редакция секретов в логах ────────────────────────────────────────────


@pytest.fixture
def captured_logs():
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


async def test_share_token_never_reaches_the_log(tmp_path, monkeypatch, captured_logs):
    bin_dir = tmp_path / "bin"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    write_fake_gallery_dl(bin_dir, files=1, exit_code=0)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    monkeypatch.setattr(downloader.settings, "COOKIES_FILE", "")

    secret = "PRIVATESHARETOKENVALUE"
    # Бриф писал `_try_gallery_dl(url, "abc123")` — на момент брифа функция
    # принимала 2 аргумента. Task 12 (коммит 980e44a, до этой задачи) добавила
    # параметр `platform` (нужен для _gallery_dl_item_limit) — сигнатура сейчас
    # `_try_gallery_dl(url, platform, filename)`. Подставляем "instagram" вторым
    # аргументом и переносим "abc123" на место `filename`, где оно и было по смыслу.
    await _try_gallery_dl(f"https://www.instagram.com/p/DBc1/?igsh={secret}", "instagram", "abc123")

    joined = "\n".join(captured_logs)
    assert "instagram.com/p/DBc1" in joined  # сама ссылка осталась читаемой
    assert secret not in joined
    assert "<redacted>" in joined
