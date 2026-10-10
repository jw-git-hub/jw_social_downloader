"""Сквозные сценарии Instagram-ветки `download_media` вокруг перекодирования:
сохранность исходника при сорванной повторной загрузке, единственный ffprobe на
файл, отмена задачи, неизвестная длительность, повторное использование
info-json и статус перекодирования. Поддельные утилиты — из test_video_transcode.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.services import downloader, video_transcode
from bot.services.downloader import DownloadResult, _build_command, download_media
from bot.services.video_transcode import TRANSCODE_MAX_DURATION_SEC, build_transcode_cmd
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
