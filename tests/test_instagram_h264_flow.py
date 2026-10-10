"""Сквозные сценарии Instagram-ветки `download_media` вокруг перекодирования:
сохранность исходника при сорванной повторной загрузке, единственный ffprobe на
файл, отмена задачи, неизвестная длительность, повторное использование
info-json и статус перекодирования. Поддельные утилиты — из test_video_transcode.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

from bot.services import downloader, video_transcode
from bot.services.downloader import DownloadResult, _build_command, download_media
from bot.services.media_probe import MediaInfo
from bot.services.video_transcode import (
    HDR_SECONDS_PER_VIDEO_SECOND,
    TRANSCODE_MAX_DURATION_SEC,
    TRANSCODE_SECONDS_PER_VIDEO_SECOND,
    build_transcode_cmd,
    estimate_transcode_seconds,
)
from bot.services.ytdlp_progress import DownloadPhase
from tests.test_video_transcode import _URL, _calls, _install_tools

LONG_DURATION = str(TRANSCODE_MAX_DURATION_SEC + 60)


def _file_texts(directory: Path) -> list[str]:
    return sorted(path.read_text().split()[0] for path in directory.iterdir())


# ── A. исходник не теряется ─────────────────────────────────────────────

async def test_original_is_delivered_when_h264_refetch_fails(tmp_path, monkeypatch):
    dl_dir, _log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration=LONG_DURATION, retry_fails=True)
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert Path(result.file_path).read_text().startswith("vp9")
    assert _file_texts(dl_dir) == ["vp9"]


async def test_original_is_delivered_and_refetch_removed_when_refetch_is_not_h264(tmp_path, monkeypatch):
    dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration=LONG_DURATION, retry_codec="vp9")
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert "ffmpeg" not in _calls(log)
    assert _file_texts(dl_dir) == ["vp9"]


async def test_successful_refetch_replaces_original_without_leftovers(tmp_path, monkeypatch):
    dl_dir, _log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration=LONG_DURATION)
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert _file_texts(dl_dir) == ["h264"]


# ── B. info-json без повторного извлечения ──────────────────────────────

def test_first_pass_writes_info_json_and_h264_pass_loads_it(tmp_path):
    first = _build_command(_URL, "instagram", tmp_path / "o.%(ext)s", None)
    reuse = _build_command(
        _URL, "instagram", tmp_path / "o.%(ext)s", None, h264_only=True, info_json_path=tmp_path / "a.info.json",
    )
    assert "--write-info-json" in first
    assert "--write-info-json" not in reuse
    assert reuse[reuse.index("--load-info-json") + 1] == str(tmp_path / "a.info.json")
    assert _URL not in reuse


async def test_long_reel_refetch_reuses_info_json_and_cleans_it_up(tmp_path, monkeypatch):
    dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration=LONG_DURATION)
    await download_media(_URL, "instagram")
    assert "loaded-info-json" in _calls(log)
    assert not list(dl_dir.glob("*.json"))


# ── F. ffprobe не дублируется ───────────────────────────────────────────

async def test_h264_instagram_video_is_probed_once(tmp_path, monkeypatch):
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="h264", duration="8.4")
    await download_media(_URL, "instagram")
    assert _calls(log).count("ffprobe") == 1


async def test_transcoded_instagram_video_is_probed_again_only_for_the_log(tmp_path, monkeypatch):
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4")
    await download_media(_URL, "instagram")
    assert _calls(log).count("ffprobe") == 2


# ── G. мелочи ───────────────────────────────────────────────────────────

def test_ffmpeg_reads_only_local_files():
    cmd = build_transcode_cmd(Path("in.mp4"), Path("out.mp4"), copy_audio=True)
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"
    assert cmd.index("-protocol_whitelist") < cmd.index("-i")


async def test_unknown_duration_is_treated_as_too_long_to_transcode(tmp_path, monkeypatch):
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="0")
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert "ffmpeg" not in _calls(log)
    assert len([c for c in _calls(log) if c.startswith("ytdlp")]) == 2


async def test_cancelled_transcode_leaves_no_files(tmp_path, monkeypatch):
    dl_dir, _log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4", ffmpeg_sleep=5.0)
    task = asyncio.create_task(download_media(_URL, "instagram"))
    await asyncio.sleep(1.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert list(dl_dir.iterdir()) == []


def test_download_result_lists_every_path_once():
    result = DownloadResult(file_path="/a.mp4", file_paths=["/a.mp4", "/b.mp4"], success=True)
    assert result.all_paths() == ["/a.mp4", "/b.mp4"]
    assert DownloadResult(file_path="/a.mp4", success=True).all_paths() == ["/a.mp4"]
    assert DownloadResult(success=False).all_paths() == []


# ── E. статус перекодирования ───────────────────────────────────────────

async def _collect_statuses(tmp_path, monkeypatch, **tools):
    _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4", **tools)
    statuses = []
    await download_media(_URL, "instagram", statuses.append)
    return [s for s in statuses if s.phase is DownloadPhase.CONVERTING]


async def test_converting_status_carries_resolution_and_size(tmp_path, monkeypatch):
    converting = await _collect_statuses(tmp_path, monkeypatch)
    assert converting
    plan = converting[0].plan
    assert plan.chosen_height == 1080 and plan.chosen_mb is not None


async def test_converting_eta_counts_down(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "CONVERT_STATUS_INTERVAL_SEC", 0.1)
    monkeypatch.setattr(video_transcode, "TRANSCODE_SECONDS_PER_VIDEO_SECOND", 1.0)
    converting = await _collect_statuses(tmp_path, monkeypatch, ffmpeg_sleep=1.0)
    etas = [s.eta_sec for s in converting]
    assert len(etas) >= 3
    assert etas == sorted(etas, reverse=True) and etas[0] > etas[-1]


async def test_converting_eta_includes_the_queue_ahead(tmp_path, monkeypatch):
    monkeypatch.setattr(video_transcode, "TRANSCODE_SECONDS_PER_VIDEO_SECOND", 10.0)
    _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4", ffmpeg_sleep=0.5)
    first, second = [], []
    await asyncio.gather(
        download_media(_URL, "instagram", first.append),
        download_media(_URL, "instagram", second.append),
    )
    first_eta = next(s.eta_sec for s in first if s.phase is DownloadPhase.CONVERTING)
    second_eta = next(s.eta_sec for s in second if s.phase is DownloadPhase.CONVERTING)
    assert max(first_eta, second_eta) >= 2 * min(first_eta, second_eta) * 0.99


# ── I1. HDR медленнее обычного перекодирования ──────────────────────────

TONEMAP_CONSTANTS_BUDGET_SEC = TRANSCODE_MAX_DURATION_SEC * TRANSCODE_SECONDS_PER_VIDEO_SECOND
HDR_TRANSFER = "smpte2084"


def test_hdr_estimate_uses_the_slower_tonemap_rate():
    sdr = MediaInfo(True, True, 10.0, 1080, 1920, "vp9", "aac", "")
    hdr = MediaInfo(True, True, 10.0, 1080, 1920, "vp9", "aac", HDR_TRANSFER)
    assert estimate_transcode_seconds(sdr) == 10.0 * TRANSCODE_SECONDS_PER_VIDEO_SECOND
    assert estimate_transcode_seconds(hdr) == 10.0 * HDR_SECONDS_PER_VIDEO_SECOND


async def test_long_hdr_post_is_refetched_while_same_length_sdr_is_transcoded(tmp_path, monkeypatch):
    duration = str(int(TONEMAP_CONSTANTS_BUDGET_SEC / HDR_SECONDS_PER_VIDEO_SECOND) + 10)
    (tmp_path / "hdr").mkdir()
    (tmp_path / "sdr").mkdir()
    _dl_dir, hdr_log = _install_tools(tmp_path / "hdr", monkeypatch, codec="vp9", duration=duration, transfer=HDR_TRANSFER)
    await download_media(_URL, "instagram")
    assert "ffmpeg" not in _calls(hdr_log)
    assert len([c for c in _calls(hdr_log) if c.startswith("ytdlp")]) == 2

    _dl_dir, sdr_log = _install_tools(tmp_path / "sdr", monkeypatch, codec="vp9", duration=duration)
    await download_media(_URL, "instagram")
    assert _calls(sdr_log).count("ffmpeg") == 1


async def test_failed_tonemap_is_retried_once_without_tonemap(tmp_path, monkeypatch):
    _dl_dir, log = _install_tools(
        tmp_path, monkeypatch, codec="vp9", duration="8.4", transfer=HDR_TRANSFER, fail_on_tonemap=True,
    )
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert _calls(log).count("ffmpeg") == 2
    assert Path(result.file_path).read_text().startswith("h264")


async def test_failed_tonemap_and_failed_retry_is_a_normal_download_error(tmp_path, monkeypatch):
    dl_dir, log = _install_tools(
        tmp_path, monkeypatch, codec="vp9", duration="8.4", transfer=HDR_TRANSFER, ffmpeg_fails=True,
    )
    result = await download_media(_URL, "instagram")
    assert result.success is False
    assert _calls(log).count("ffmpeg") == 2
    assert list(dl_dir.iterdir()) == []


async def test_status_countdown_is_reset_to_the_cheaper_estimate_after_tonemap_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "CONVERT_STATUS_INTERVAL_SEC", 0.1)
    monkeypatch.setattr(video_transcode, "HDR_SECONDS_PER_VIDEO_SECOND", 100.0)
    monkeypatch.setattr(downloader, "TRANSCODE_MAX_WORK_SEC", 10_000.0)
    monkeypatch.setattr(video_transcode, "TRANSCODE_SECONDS_PER_VIDEO_SECOND", 1.0)
    _install_tools(
        tmp_path, monkeypatch, codec="vp9", duration="8.4", transfer=HDR_TRANSFER,
        fail_on_tonemap=True, ffmpeg_sleep=0.4,
    )
    statuses = []
    await download_media(_URL, "instagram", statuses.append)
    etas = [s.eta_sec for s in statuses if s.phase is DownloadPhase.CONVERTING]
    assert etas[0] > 500
    assert etas[-1] < 20


# ── I2. нечитаемый повтор не заменяет исходник ──────────────────────────

async def test_unreadable_refetch_does_not_replace_the_original(tmp_path, monkeypatch):
    dl_dir, _log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration=LONG_DURATION, retry_codec="broken")
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert Path(result.file_path).read_text().startswith("vp9")
    assert _file_texts(dl_dir) == ["vp9"]


# ── M1. info-json не остаётся на диске ──────────────────────────────────

def test_playlist_metafiles_are_not_written():
    cmd = _build_command(_URL, "instagram", Path("/tmp/o.%(ext)s"), None)
    assert "--no-write-playlist-metafiles" in cmd


async def test_info_json_is_removed_when_ytdlp_found_no_media_and_gallery_dl_took_over(tmp_path, monkeypatch):
    dl_dir, _log = _install_tools(tmp_path, monkeypatch, codec="h264", duration="8.4", json_without_media=True)
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert not list(dl_dir.glob("*.json"))


async def test_info_json_of_every_playlist_item_is_removed(tmp_path, monkeypatch):
    dl_dir, _log = _install_tools(tmp_path, monkeypatch, codec="h264", duration="8.4", files_count=3)
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert not list(dl_dir.glob("*.json"))


# ── M5. без H.264 повтор не качает VP9 второй раз ───────────────────────

def test_h264_only_selector_has_no_catch_all_tail():
    assert not downloader.INSTAGRAM_H264_ONLY_SELECTOR.endswith("/bv*+ba/b")


class _CountingHandler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):
        type(self).hits.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


async def test_refetch_without_any_h264_format_downloads_nothing(tmp_path, monkeypatch):
    """Реальный yt-dlp на info-json, где есть только VP9: команда завершается чисто,
    ничего не качается, `_choose_after_refetch` отдаёт исходник."""
    _CountingHandler.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    info = {
        "id": "x", "title": "t", "extractor": "instagram", "extractor_key": "Instagram", "webpage_url": base,
        "duration": 150,
        "formats": [
            {"format_id": "vp9", "url": f"{base}/v", "ext": "mp4", "protocol": "https", "vcodec": "vp09.00.40.08",
             "acodec": "none", "width": 1080, "height": 1920, "fps": 30},
            {"format_id": "aud", "url": f"{base}/a", "ext": "m4a", "protocol": "https", "vcodec": "none",
             "acodec": "mp4a.40.5"},
        ],
    }
    info_json = tmp_path / "orig.info.json"
    info_json.write_text(json.dumps(info))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", tmp_path / "dl")
    try:
        retry = await downloader._download_media_once(
            _URL, "instagram", None, h264_only=True, info_json_path=info_json,
        )
    finally:
        server.shutdown()
    assert retry.success is False
    assert _CountingHandler.hits == []
    assert not list((tmp_path / "dl").iterdir())


# ── M8. метка качества соответствует отправляемому файлу ────────────────

def _last_plan_height(statuses):
    return [s.plan.chosen_height for s in statuses if s.plan is not None][-1]


async def test_quality_label_returns_to_the_original_when_refetch_fails(tmp_path, monkeypatch):
    _install_tools(tmp_path, monkeypatch, codec="vp9", duration=LONG_DURATION, retry_fails=True)
    statuses = []
    await download_media(_URL, "instagram", statuses.append)
    assert _last_plan_height(statuses) == 1080


async def test_quality_label_returns_to_the_original_when_refetch_is_rejected(tmp_path, monkeypatch):
    _install_tools(tmp_path, monkeypatch, codec="vp9", duration=LONG_DURATION, retry_codec="vp9")
    statuses = []
    await download_media(_URL, "instagram", statuses.append)
    assert _last_plan_height(statuses) == 1080


async def test_quality_label_follows_an_accepted_refetch(tmp_path, monkeypatch):
    _install_tools(tmp_path, monkeypatch, codec="vp9", duration=LONG_DURATION)
    statuses = []
    await download_media(_URL, "instagram", statuses.append)
    assert _last_plan_height(statuses) == 720
