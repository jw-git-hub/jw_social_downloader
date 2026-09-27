"""Тексты статус-сообщения — примеры и краевые случаи из брифа (раздел 3)."""

from __future__ import annotations

from bot.services.progress_texts import (
    PREPARING_TEXT,
    QUEUED_TEXT,
    download_status_text,
    downgrade_note,
    format_eta,
    format_size,
    limit_line,
    limits_block,
    progress_bar,
    quality_label,
    quality_short,
    upload_status_text,
)
from bot.services.ytdlp_progress import DownloadPhase, DownloadStatus, FormatPlan

FOUR_K_PLAN = FormatPlan(chosen_height=2160, chosen_mb=904.1, best_height=2160, best_mb=899.0)
HOURLY_PLAN = FormatPlan(chosen_height=1080, chosen_mb=888.8, best_height=2160, best_mb=5234.0)


# ── форматирование ──


def test_format_size_examples():
    assert format_size(904.1) == "904 МБ"
    assert format_size(888.8) == "889 МБ"
    assert format_size(1500) == "1.5 ГБ"
    assert format_size(5234) == "5.1 ГБ"
    assert format_size(50) == "50 МБ"
    assert format_size(0.3) == "1 МБ"


def test_quality_short_and_label():
    assert quality_short(2160) == "4K"
    assert quality_short(1080) == "1080p"
    assert quality_label(4320) == "8K · 4320p"
    assert quality_label(2160) == "4K · 2160p"
    assert quality_label(1080) == "1080p"


def test_progress_bar_fills_by_tens():
    assert progress_bar(0.52) == "▓▓▓▓▓░░░░░"
    assert progress_bar(0.99) == "▓▓▓▓▓▓▓▓▓░"
    assert progress_bar(0.0) == "░░░░░░░░░░"
    assert progress_bar(1.0) == "▓▓▓▓▓▓▓▓▓▓"


def test_format_eta_examples():
    assert format_eta(30) == "меньше минуты"
    assert format_eta(70) == "~1 мин"
    assert format_eta(100) == "~2 мин"


# ── пояснение о понижении качества ──


def test_downgrade_note_shown_when_best_exceeds_limit():
    note = downgrade_note(HOURLY_PLAN, limit_mb=1500)
    assert "В 4K ролик весит от 5.1 ГБ" in note
    assert "до 1.5 ГБ" in note
    assert note.endswith("— 1080p.")


def test_downgrade_note_uses_limit_in_mb_below_gib_threshold():
    note = downgrade_note(HOURLY_PLAN, limit_mb=50)
    assert "до 50 МБ" in note


def test_downgrade_note_none_when_best_equals_chosen():
    assert downgrade_note(FOUR_K_PLAN, limit_mb=1500) is None


def test_downgrade_note_none_when_best_mb_unknown():
    plan = FormatPlan(chosen_height=1080, chosen_mb=888.8, best_height=2160, best_mb=None)
    assert downgrade_note(plan, limit_mb=1500) is None


def test_downgrade_note_none_when_best_fits_limit():
    plan = FormatPlan(chosen_height=1080, chosen_mb=888.8, best_height=2160, best_mb=1000.0)
    assert downgrade_note(plan, limit_mb=1500) is None


def test_downgrade_note_none_for_none_plan():
    assert downgrade_note(None, limit_mb=1500) is None


# ── download_status_text ──


def test_download_status_text_none_is_preparing():
    assert download_status_text(None, limit_mb=1500) == PREPARING_TEXT


def test_download_status_text_preparing_without_plan_is_preparing_text():
    status = DownloadStatus(phase=DownloadPhase.PREPARING, plan=None)
    assert download_status_text(status, limit_mb=1500) == PREPARING_TEXT


def test_download_status_text_preparing_with_plan_shows_zero_percent():
    status = DownloadStatus(phase=DownloadPhase.PREPARING, plan=FOUR_K_PLAN)
    text = download_status_text(status, limit_mb=1500)
    assert "⬇️" in text
    assert "░░░░░░░░░░ 0%" in text


def test_download_status_text_4k_fits_no_note():
    status = DownloadStatus(
        phase=DownloadPhase.DOWNLOADING, plan=FOUR_K_PLAN, fraction=0.52, downloaded_mb=450.0, eta_sec=70,
    )
    text = download_status_text(status, limit_mb=1500)
    assert "⬇️" in text
    assert "4K · 2160p" in text
    assert "904 МБ" in text
    assert "▓▓▓▓▓░░░░░ 52%" in text
    assert "осталось ~1 мин" in text
    assert "ℹ️" not in text


def test_download_status_text_downgrade_shows_note():
    status = DownloadStatus(
        phase=DownloadPhase.DOWNLOADING, plan=HOURLY_PLAN, fraction=0.27, downloaded_mb=240.0, eta_sec=180,
    )
    text = download_status_text(status, limit_mb=1500)
    assert "1080p" in text
    assert "889 МБ" in text
    assert "В 4K ролик весит от 5.1 ГБ" in text
    assert "до 1.5 ГБ" in text
    assert "— 1080p." in text


def test_download_status_text_without_eta():
    status = DownloadStatus(
        phase=DownloadPhase.DOWNLOADING, plan=FOUR_K_PLAN, fraction=0.27, downloaded_mb=240.0, eta_sec=None,
    )
    text = download_status_text(status, limit_mb=1500)
    assert "▓▓░░░░░░░░ 27%" in text
    assert "осталось" not in text


def test_download_status_text_unknown_total_shows_downloaded_mb():
    status = DownloadStatus(
        phase=DownloadPhase.DOWNLOADING, plan=FOUR_K_PLAN, fraction=None, downloaded_mb=120.0, eta_sec=None,
    )
    text = download_status_text(status, limit_mb=1500)
    assert "Скачано 120 МБ" in text


def test_download_status_text_merging_shows_eta():
    status = DownloadStatus(phase=DownloadPhase.MERGING, plan=HOURLY_PLAN, eta_sec=70)
    text = download_status_text(status, limit_mb=1500)
    assert "🎬" in text
    assert "Ещё ~1 мин." in text


# ── upload_status_text ──


def test_upload_status_text_in_progress():
    text = upload_status_text(FOUR_K_PLAN, size_mb=904.1, elapsed_sec=60, expected_sec=188, limit_mb=1500)
    assert "▓▓▓░░░░░░░ ≈31%" in text
    assert "осталось ~2 мин" in text


def test_upload_status_text_past_expected_shows_almost_done():
    text = upload_status_text(FOUR_K_PLAN, size_mb=904.1, elapsed_sec=200, expected_sec=188, limit_mb=1500)
    assert "почти готово" in text
    assert "%" not in text


def test_upload_status_text_without_expected_has_no_bar():
    text = upload_status_text(FOUR_K_PLAN, size_mb=904.1, elapsed_sec=None, expected_sec=None, limit_mb=1500)
    assert "▓" not in text
    assert "░" not in text
    assert "4K · 2160p" in text
    assert "904 МБ" in text


def test_upload_status_text_small_file_without_plan_shows_size_only():
    text = upload_status_text(None, size_mb=12.0, elapsed_sec=None, expected_sec=None, limit_mb=1500)
    assert "📤" in text
    assert "12 МБ" in text
    assert "4K" not in text


def test_upload_status_text_unknown_size_skips_size_part():
    text = upload_status_text(None, size_mb=None, elapsed_sec=None, expected_sec=None, limit_mb=1500)
    assert "МБ" not in text
    assert "ГБ" not in text


def test_upload_status_text_carries_downgrade_note():
    text = upload_status_text(HOURLY_PLAN, size_mb=888.8, elapsed_sec=None, expected_sec=None, limit_mb=1500)
    assert "ℹ️" in text


# ── приветствие/помощь ──


def test_limit_line_shows_gib_for_large_limit():
    assert "1.5 ГБ" in limit_line(1500)


def test_limit_line_shows_mb_for_cloud_limit():
    assert "50 МБ" in limit_line(50)


def test_limits_block_mentions_examples():
    text = limits_block(1500)
    assert "Ограничения" in text
    assert "4K" in text
    assert "1080p" in text


def test_queued_text_is_a_fixed_constant():
    assert "Жду своей очереди" in QUEUED_TEXT
