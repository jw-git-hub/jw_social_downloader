import pytest
from loguru import logger

from bot.utils.log_guard import mask_secrets, scrub_log_file, setup_logging

# Заведомо ненастоящий секрет: цифровая часть и 35 символов «X».
# В публичном репозитории реальных значений быть не может.
FAKE_SECRET = "424242:" + "X" * 35


@pytest.fixture
def isolated_logger():
    """Loguru глобален — снимаем все синки до теста и после него."""
    logger.remove()
    yield logger
    logger.remove()
    logger.configure(patcher=None)


def test_bot_secret_is_masked_but_numeric_id_survives():
    text = f"POST https://api.telegram.org/bot{FAKE_SECRET}/sendMediaGroup"
    masked = mask_secrets(text)
    assert "X" * 35 not in masked
    assert "424242:<redacted>" in masked
    assert "sendMediaGroup" in masked


def test_share_tokens_in_query_string_are_masked():
    text = "Starting download | url=https://www.instagram.com/reel/AbC/?igsh=MXY5eg%3D%3D&utm_source=ig"
    masked = mask_secrets(text)
    assert "MXY5eg" not in masked
    assert "igsh=<redacted>" in masked
    # Не-секретные параметры не трогаем.
    assert "utm_source=ig" in masked


def test_masking_is_idempotent():
    once = mask_secrets(f"token={FAKE_SECRET} url=https://x/y?stkn=abcdef")
    assert mask_secrets(once) == once


def test_masking_never_raises_on_odd_input():
    assert mask_secrets("") == ""
    assert mask_secrets("нет тут секретов") == "нет тут секретов"
    assert isinstance(mask_secrets(12345), str)


def test_setup_logging_masks_every_sink(tmp_path, isolated_logger):
    log_file = tmp_path / "bot.log"
    setup_logging(str(log_file))
    logger.error("Failed to send file: POST https://api.telegram.org/bot{}/sendVideo", FAKE_SECRET)
    logger.remove()  # закрываем файловый синк, чтобы содержимое точно дошло до диска

    written = log_file.read_text(encoding="utf-8")
    assert "X" * 35 not in written
    assert "424242:<redacted>" in written


def test_setup_logging_drops_the_default_debug_sink(tmp_path, isolated_logger):
    log_file = tmp_path / "bot.log"
    setup_logging(str(log_file))
    logger.debug("Using cookies file: /tmp/jw_downloads/jw_cookies_abc/cookies.txt")
    logger.remove()

    written = log_file.read_text(encoding="utf-8")
    assert "Using cookies file" not in written


def test_scrub_log_file_rewrites_existing_log_in_place(tmp_path):
    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "первая строка без секретов\n"
        f"вторая строка: https://api.telegram.org/bot{FAKE_SECRET}/getUpdates\n"
        "третья строка без секретов\n",
        encoding="utf-8",
    )

    changed = scrub_log_file(log_file)

    content = log_file.read_text(encoding="utf-8")
    assert changed == 1
    assert "X" * 35 not in content
    assert "424242:<redacted>" in content
    assert content.count("\n") == 3
    assert "первая строка без секретов" in content
