"""Разбор строк JWPLAN/JWPROG/JWPP: план формата и прогресс скачивания.

Фикстуры строк — из живого прогона 2026-09-27 (см. бриф Задачи 1), не
перепроверяются заново, а используются как контракт формата.
"""

from __future__ import annotations

import pytest

from bot.services.ytdlp_progress import (
    ETA_MIN_ELAPSED_SEC,
    MERGE_MIB_PER_SEC,
    MIB,
    DownloadPhase,
    DownloadTracker,
    nominal_height,
)

# ── nominal_height ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (3840, 2160, 2160),
        (1080, 1920, 1080),
        (3840, 1600, 2160),  # кино 2.4:1 — по длинной стороне
        (1440, 1080, 1080),
        (608, 1080, 480),
        (7680, 4320, 4320),
        (1920, 1080, 1080),
        (None, 1080, None),
        (0, 0, None),
        (100, 50, 50),
    ],
)
def test_nominal_height(width, height, expected):
    assert nominal_height(width, height) == expected


# ── JWPLAN: фикстуры живого прогона ──────────────────────────────────────

PLAN_4K = (
    'JWPLAN {"width": 3840, "height": 2160, "filesize_approx": 948007326}\t'
    '[{"width": 48, "height": 27, "vcodec": "none"}, '
    '{"width": 3840, "height": 2160, "vcodec": "vp9", "filesize": 942927341}, '
    '{"vcodec": "none", "filesize": 5079985}]'
)

PLAN_DOWNGRADED_1080P = (
    'JWPLAN {"width": 1920, "height": 1080, "filesize_approx": 931982591}\t'
    '[{"width": 3840, "height": 2160, "vcodec": "av01.0.12M.08", "filesize": 5488246784}, '
    '{"width": 3840, "height": 2160, "vcodec": "vp9", "filesize": 7244611584}, '
    '{"width": 1920, "height": 1080, "vcodec": "vp9", "filesize": 873505486}, '
    '{"vcodec": "none", "filesize": 58477105}]'
)


def test_plan_4k_matches_live_fixture():
    tracker = DownloadTracker()
    tracker.feed(PLAN_4K)
    plan = tracker.snapshot().plan
    assert plan.chosen_height == 2160
    assert plan.chosen_mb == pytest.approx(904.1, abs=0.1)
    assert plan.best_height == 2160
    assert plan.best_mb == pytest.approx(899.2, abs=0.1)


def test_plan_downgraded_1080p_matches_live_fixture():
    tracker = DownloadTracker()
    tracker.feed(PLAN_DOWNGRADED_1080P)
    plan = tracker.snapshot().plan
    assert plan.chosen_height == 1080
    assert plan.chosen_mb == pytest.approx(888.8, abs=0.1)
    assert plan.best_height == 2160
    assert plan.best_mb == pytest.approx(5234.0, abs=0.1)


def test_plan_with_broken_formats_json_still_sets_top_level_plan():
    tracker = DownloadTracker()
    tracker.feed('JWPLAN {"width": 1920, "height": 1080, "filesize": 100}\tNOT_JSON[')
    plan = tracker.snapshot().plan
    assert plan is not None
    assert plan.chosen_height == 1080
    assert plan.best_height is None
    assert plan.best_mb is None


def test_completely_broken_plan_line_is_consumed_but_ignored():
    tracker = DownloadTracker()
    assert tracker.feed("JWPLAN x") is True
    assert tracker.snapshot().plan is None


# ── feed: что является служебной строкой ─────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "[download] Destination: /srv/jw_downloads/x.f315.webm",
        "[download] File is larger than max-filesize (10201228 bytes > 1048576 bytes). Aborting.",
        "ERROR: something went wrong",
        "",
    ],
)
def test_feed_returns_false_for_non_service_lines(line):
    assert DownloadTracker().feed(line) is False


# ── JWPROG: две дорожки подряд (план 4K) ─────────────────────────────────


def test_two_tracks_progress_is_monotonic_and_capped():
    tracker = DownloadTracker()
    tracker.feed(PLAN_4K)

    tracker.feed("JWPROG|315|downloading|471463670|942927341|NA")
    assert tracker.snapshot().fraction == pytest.approx(0.497, abs=0.001)

    tracker.feed("JWPROG|315|finished|942927341|942927341|NA")
    assert tracker.snapshot().fraction == pytest.approx(0.99, abs=0.0001)

    fraction_after_315 = tracker.snapshot().fraction
    tracker.feed("JWPROG|140|downloading|1024|5079985|NA")
    assert tracker.snapshot().fraction >= fraction_after_315

    tracker.feed("JWPROG|140|finished|5079985|5079985|NA")
    assert tracker.snapshot().fraction == pytest.approx(0.99, abs=0.0001)


def test_progress_without_plan_uses_sum_of_known_totals():
    tracker = DownloadTracker()
    tracker.feed("JWPROG|315|downloading|500|1000|NA")
    assert tracker.snapshot().fraction == pytest.approx(0.5)

    # Новая дорожка временно снижает сырое отношение done/expected —
    # процент не должен упасть.
    tracker.feed("JWPROG|140|downloading|1|10|NA")
    assert tracker.snapshot().fraction == pytest.approx(0.5)


def test_fragmented_download_uses_total_bytes_estimate_when_total_bytes_is_na():
    """DASH-фрагменты (formats=dashy, см. поправку главной сессии): у
    фрагментной закачки `progress.total_bytes` обычно пуст — процент должен
    считаться по `total_bytes_estimate`. HLS-строка из брифа — тот же случай
    отсутствующего total_bytes.
    """
    tracker = DownloadTracker()
    tracker.feed("JWPROG|628|downloading|500|NA|1000")
    status = tracker.snapshot()
    assert status.fraction == pytest.approx(0.5)
    assert status.phase == DownloadPhase.DOWNLOADING


def test_fragmented_download_percent_grows_monotonically_without_total_bytes():
    """Явная проверка деградации #2: несколько строк подряд без total_bytes
    (только total_bytes_estimate) должны монотонно расти, а не выдавать NA
    или откатываться назад."""
    tracker = DownloadTracker()
    fractions = []
    for downloaded in (1_000_000, 4_000_000, 8_000_000, 10_000_000):
        tracker.feed(f"JWPROG|dashy-fmt|downloading|{downloaded}|NA|10000000")
        fractions.append(tracker.snapshot().fraction)

    assert all(f is not None for f in fractions)
    assert fractions == sorted(fractions)
    assert fractions[-1] == pytest.approx(1.0, abs=0.01) or fractions[-1] <= 0.99


def test_downloaded_status_without_total_reports_downloaded_bytes_only():
    tracker = DownloadTracker()
    tracker.feed("JWPROG|315|downloading|NA|NA|NA")
    status = tracker.snapshot()
    assert status.fraction is None
    assert status.downloaded_mb == 0.0


def test_new_plan_resets_percent_for_next_playlist_item():
    tracker = DownloadTracker()
    tracker.feed(PLAN_4K)
    tracker.feed("JWPROG|315|finished|942927341|942927341|NA")
    assert tracker.snapshot().fraction == pytest.approx(0.99, abs=0.0001)

    tracker.feed(PLAN_DOWNGRADED_1080P)
    status = tracker.snapshot()
    assert status.fraction is None
    assert status.phase == DownloadPhase.PREPARING
    assert status.plan.chosen_height == 1080


# ── ETA ────────────────────────────────────────────────────────────────


def test_eta_needs_minimum_elapsed_time():
    clock = iter([0.0, 0.0, 10.0, 10.0]).__next__
    tracker = DownloadTracker(clock=clock)
    tracker.feed('JWPLAN {"width": 1920, "height": 1080, "filesize": ' + str(100 * MIB) + "}")

    tracker.feed(f"JWPROG|315|downloading|{10 * MIB}|NA|NA")
    assert tracker.snapshot().eta_sec is None

    tracker.feed(f"JWPROG|315|downloading|{20 * MIB}|NA|NA")
    assert tracker.snapshot().eta_sec == pytest.approx(40.0, abs=0.5)


def test_eta_min_elapsed_constant_is_five_seconds():
    assert ETA_MIN_ELAPSED_SEC == 5.0


# ── JWPP: склейка ─────────────────────────────────────────────────────────


def test_merger_started_switches_to_merging_phase_with_eta():
    tracker = DownloadTracker()
    tracker.feed('JWPLAN {"width": 1920, "height": 1080, "filesize": ' + str(int(130 * MIB)) + "}")
    tracker.feed("JWPP|Merger|started")
    status = tracker.snapshot()
    assert status.phase == DownloadPhase.MERGING
    assert status.eta_sec == pytest.approx(130 / MERGE_MIB_PER_SEC, abs=0.01)


def test_move_files_started_does_not_change_phase():
    tracker = DownloadTracker()
    tracker.feed("JWPROG|315|downloading|500|1000|NA")
    tracker.feed("JWPP|MoveFiles|started")
    assert tracker.snapshot().phase == DownloadPhase.DOWNLOADING


# ── Контракт с настоящим yt-dlp (офлайн, без сети) ───────────────────────


def test_ytdlp_parse_options_disables_quiet_and_enables_progress():
    yt_dlp = pytest.importorskip("yt_dlp")
    from bot.services.ytdlp_progress import YTDLP_PROGRESS_ARGS

    parsed = yt_dlp.parse_options(list(YTDLP_PROGRESS_ARGS) + ["https://example.invalid/x"])
    opts = parsed.ydl_opts
    assert opts["quiet"] is False
    assert opts["noprogress"] is False
    assert opts.get("simulate") is None
    assert opts["progress_with_newline"] is True
    assert "before_dl" in opts["forceprint"]


def test_ytdlp_evaluate_outtmpl_plan_template_feeds_tracker_correctly():
    yt_dlp = pytest.importorskip("yt_dlp")
    from bot.services.ytdlp_progress import PLAN_TEMPLATE

    info = {
        "id": "abc123",
        "width": 1920,
        "height": 1080,
        "filesize_approx": 900 * MIB,
        "formats": [
            {"width": 3840, "height": 2160, "vcodec": "vp9", "filesize": 5000 * MIB},
            {"vcodec": "none", "filesize": 10 * MIB},
        ],
    }
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        line = ydl.evaluate_outtmpl(PLAN_TEMPLATE, info)

    tracker = DownloadTracker()
    assert tracker.feed(line) is True
    plan = tracker.snapshot().plan
    assert plan.chosen_height == 1080
    assert plan.best_height == 2160
    assert plan.best_mb == pytest.approx(5000.0, abs=0.1)


def test_ytdlp_evaluate_outtmpl_progress_template_feeds_tracker_correctly():
    yt_dlp = pytest.importorskip("yt_dlp")
    from bot.services.ytdlp_progress import PROGRESS_TEMPLATE

    context = {
        "info": {"format_id": "315"},
        "progress": {
            "status": "downloading",
            "downloaded_bytes": 500,
            "total_bytes": 1000,
            "total_bytes_estimate": None,
        },
    }
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        line = ydl.evaluate_outtmpl(PROGRESS_TEMPLATE, context)

    tracker = DownloadTracker()
    assert tracker.feed(line) is True
    assert tracker.snapshot().fraction == pytest.approx(0.5)


def test_ytdlp_evaluate_outtmpl_postprocess_template_feeds_tracker_correctly():
    yt_dlp = pytest.importorskip("yt_dlp")
    from bot.services.ytdlp_progress import POSTPROCESS_TEMPLATE

    context = {"progress": {"postprocessor": "Merger", "status": "started"}}
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        line = ydl.evaluate_outtmpl(POSTPROCESS_TEMPLATE, context)

    tracker = DownloadTracker()
    assert tracker.feed(line) is True
    assert tracker.snapshot().phase == DownloadPhase.MERGING
