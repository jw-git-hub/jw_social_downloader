from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage

from bot.services.sending import (
    PROBABLY_DELIVERED_AFTER_SEC,
    SERVER_IDLE_TIMEOUT_SEC,
    SendVerdict,
    classify_send_failure,
    oversized_files,
)

MAX_ATTEMPTS = 4
_METHOD = SendMessage(chat_id=1, text="x")


def _entity_too_large() -> TelegramEntityTooLarge:
    return TelegramEntityTooLarge(method=_METHOD, message="too large")


def _network_error() -> TelegramNetworkError:
    return TelegramNetworkError(method=_METHOD, message="boom")


def _retry_after() -> TelegramRetryAfter:
    return TelegramRetryAfter(method=_METHOD, message="flood", retry_after=3)


@pytest.mark.parametrize("elapsed", [1, 999])
def test_entity_too_large_is_never_retried(elapsed):
    verdict = classify_send_failure(_entity_too_large(), elapsed, attempt=0, max_attempts=MAX_ATTEMPTS)
    assert verdict is SendVerdict.TOO_LARGE


def test_network_error_at_boundary_is_probably_delivered():
    verdict = classify_send_failure(
        _network_error(), PROBABLY_DELIVERED_AFTER_SEC, attempt=0, max_attempts=MAX_ATTEMPTS
    )
    assert verdict is SendVerdict.PROBABLY_DELIVERED


def test_network_error_just_below_boundary_is_retried():
    verdict = classify_send_failure(
        _network_error(), PROBABLY_DELIVERED_AFTER_SEC - 0.1, attempt=0, max_attempts=MAX_ATTEMPTS
    )
    assert verdict is SendVerdict.RETRY


def test_fast_network_error_on_last_attempt_gives_up():
    verdict = classify_send_failure(
        _network_error(), 1.0, attempt=MAX_ATTEMPTS - 1, max_attempts=MAX_ATTEMPTS
    )
    assert verdict is SendVerdict.GIVE_UP


@pytest.mark.parametrize("make_exc", [lambda: asyncio.TimeoutError(), lambda: aiohttp.ClientError("x")])
def test_timeout_and_client_error_short_is_retried(make_exc):
    verdict = classify_send_failure(make_exc(), 1.0, attempt=0, max_attempts=MAX_ATTEMPTS)
    assert verdict is SendVerdict.RETRY


@pytest.mark.parametrize("make_exc", [lambda: asyncio.TimeoutError(), lambda: aiohttp.ClientError("x")])
def test_timeout_and_client_error_long_is_probably_delivered(make_exc):
    verdict = classify_send_failure(
        make_exc(), PROBABLY_DELIVERED_AFTER_SEC + 1, attempt=0, max_attempts=MAX_ATTEMPTS
    )
    assert verdict is SendVerdict.PROBABLY_DELIVERED


def test_retry_after_is_retried_before_last_attempt():
    verdict = classify_send_failure(_retry_after(), 1.0, attempt=0, max_attempts=MAX_ATTEMPTS)
    assert verdict is SendVerdict.RETRY


def test_retry_after_on_last_attempt_gives_up():
    verdict = classify_send_failure(
        _retry_after(), 1.0, attempt=MAX_ATTEMPTS - 1, max_attempts=MAX_ATTEMPTS
    )
    assert verdict is SendVerdict.GIVE_UP


@pytest.mark.parametrize(
    "exc", [TelegramBadRequest(method=_METHOD, message="bad"), ValueError("oops")]
)
def test_unrelated_exceptions_give_up(exc):
    verdict = classify_send_failure(exc, 1.0, attempt=0, max_attempts=MAX_ATTEMPTS)
    assert verdict is SendVerdict.GIVE_UP


def test_probably_delivered_threshold_is_below_server_idle_timeout():
    assert PROBABLY_DELIVERED_AFTER_SEC < SERVER_IDLE_TIMEOUT_SEC


def test_oversized_files_skips_small_file(tmp_path):
    small = tmp_path / "small.mp4"
    small.write_bytes(b"x" * 1024)
    assert oversized_files([str(small)], limit_mb=1) == []


def test_oversized_files_reports_large_file(tmp_path):
    big = tmp_path / "big.mp4"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    result = oversized_files([str(big)], limit_mb=1)
    assert len(result) == 1
    path, size_mb = result[0]
    assert path == str(big)
    assert size_mb == pytest.approx(2.0, abs=0.01)


def test_oversized_files_skips_missing_path():
    assert oversized_files(["/tmp/does-not-exist-xyz.mp4"], limit_mb=1) == []


def test_oversized_files_returns_only_the_large_one(tmp_path):
    small = tmp_path / "small.mp4"
    small.write_bytes(b"x" * 1024)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    result = oversized_files([str(small), str(big)], limit_mb=1)
    assert [p for p, _ in result] == [str(big)]
