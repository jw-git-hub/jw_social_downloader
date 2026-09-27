"""Оценка скорости отправки в Telegram: скользящее окно последних 5 крупных
отправок, дефолт 4.8 МиБ/с (замеры из брифа)."""

from __future__ import annotations

from bot.services.upload_estimate import (
    DEFAULT_UPLOAD_MIB_PER_SEC,
    UPLOAD_SAMPLE_WINDOW,
    UploadRateTracker,
)


def test_default_rate_before_any_sample():
    tracker = UploadRateTracker()
    assert tracker.rate_mb_per_sec() == DEFAULT_UPLOAD_MIB_PER_SEC
    assert tracker.expected_seconds(904) == 904 / DEFAULT_UPLOAD_MIB_PER_SEC


def test_rate_after_two_samples_is_total_size_over_total_seconds():
    tracker = UploadRateTracker()
    tracker.record(904, 176)
    tracker.record(890, 193)
    assert tracker.rate_mb_per_sec() == (904 + 890) / (176 + 193)


def test_small_samples_are_ignored():
    tracker = UploadRateTracker()
    tracker.record(50, 10)  # < UPLOAD_SAMPLE_MIN_MB
    assert tracker.rate_mb_per_sec() == DEFAULT_UPLOAD_MIB_PER_SEC


def test_zero_or_negative_duration_is_ignored():
    tracker = UploadRateTracker()
    tracker.record(500, 0)
    tracker.record(500, -1)
    assert tracker.rate_mb_per_sec() == DEFAULT_UPLOAD_MIB_PER_SEC


def test_window_keeps_only_last_five_samples():
    tracker = UploadRateTracker()
    for _ in range(UPLOAD_SAMPLE_WINDOW):
        tracker.record(100, 100)  # 1 МиБ/с
    tracker.record(1000, 100)  # 10 МиБ/с — сдвигает окно
    rate = tracker.rate_mb_per_sec()
    assert rate != 1.0
    expected = (100 * (UPLOAD_SAMPLE_WINDOW - 1) + 1000) / (100 * UPLOAD_SAMPLE_WINDOW)
    assert rate == expected


def test_expected_seconds_uses_current_rate():
    tracker = UploadRateTracker()
    tracker.record(904, 176)
    tracker.record(890, 193)
    rate = tracker.rate_mb_per_sec()
    assert tracker.expected_seconds(500) == 500 / rate
