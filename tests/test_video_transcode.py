"""Instagram отдаёт лучшее разрешение в VP9, а iPhone-клиент Telegram VP9 не
показывает (картинка стоит на первом кадре). Итоговый видеофайл Instagram с
любым не-h264 кодеком обязан перекодироваться в H.264; h264 — не трогаем;
слишком длинный ролик не перекодируем, а перекачиваем в H.264.

Поддельные yt-dlp/ffprobe/ffmpeg лежат в PATH (приём из test_downloader_progress):
кодек «файла» задаёт его содержимое, обращения к ffmpeg и yt-dlp пишутся в журнал.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from loguru import logger

from bot.services import downloader, video_transcode
from bot.services.downloader import _build_command, download_media
from bot.services.media_probe import MediaInfo, probe_media
from bot.services.video_transcode import (
    TRANSCODE_MAX_DURATION_SEC,
    TranscodeError,
    build_transcode_cmd,
    is_hdr,
    needs_h264_transcode,
)

_URL = "https://www.instagram.com/reel/DbWoC_CsNcZ/"


def _info(
    video_codec: str, duration: float = 8.4, audio_codec: str = "aac", color_transfer: str = ""
) -> MediaInfo:
    return MediaInfo(True, True, duration, 1080, 1920, video_codec, audio_codec, color_transfer)


@pytest.mark.parametrize("codec", ["vp9", "av1", "hevc"])
def test_non_h264_video_needs_transcode(codec):
    assert needs_h264_transcode(_info(codec)) is True


def test_h264_video_does_not_need_transcode():
    assert needs_h264_transcode(_info("h264")) is False


def test_unreadable_or_codecless_file_is_left_alone():
    assert needs_h264_transcode(None) is False
    assert needs_h264_transcode(MediaInfo(False, True, 5.0, 0, 0, "", "aac")) is False
    assert needs_h264_transcode(_info("")) is False


def test_transcode_cmd_keeps_quality_and_copies_aac():
    cmd = build_transcode_cmd(Path("in.mp4"), Path("out.mp4"), copy_audio=True)
    joined = " ".join(cmd)
    assert "libx264" in cmd and "yuv420p" in cmd
    assert "+faststart" in cmd
    assert "-c:a copy" in joined
    assert cmd[-1] == "out.mp4"


def test_transcode_cmd_reencodes_non_aac_audio():
    cmd = build_transcode_cmd(Path("in.mp4"), Path("out.mp4"), copy_audio=False)
    assert "-c:a aac" in " ".join(cmd)
    assert "copy" not in cmd


def test_instagram_command_prefers_h264_at_equal_resolution(tmp_path):
    cmd = _build_command(_URL, "instagram", tmp_path / "o.%(ext)s", None)
    assert cmd[cmd.index("-S") + 1] == downloader.INSTAGRAM_FORMAT_SORT
    assert cmd[cmd.index("-f") + 1] == "bv*+ba/b"


def test_transcode_cmd_pads_odd_frame_size_to_even():
    cmd = build_transcode_cmd(Path("in.mp4"), Path("out.mp4"), copy_audio=True)
    assert cmd[cmd.index("-vf") + 1] == "pad=ceil(iw/2)*2:ceil(ih/2)*2"


def test_h264_only_selector_lets_sort_pick_best_resolution_among_all_non_vp9():
    selector = downloader.INSTAGRAM_H264_ONLY_SELECTOR
    assert selector.index("bv*") < selector.index("/b[")


def test_gallery_dl_h264_only_takes_merged_progressive_videos():
    plain = downloader._build_gallery_dl_cmd(_URL, "instagram", "abc", None)
    h264 = downloader._build_gallery_dl_cmd(_URL, "instagram", "abc", None, h264_only=True)
    assert "videos=merged" not in plain
    assert h264[h264.index("-o") + 1] == "videos=merged"


def test_instagram_h264_only_command_excludes_vp9_and_av1(tmp_path):
    cmd = _build_command(_URL, "instagram", tmp_path / "o.%(ext)s", None, h264_only=True)
    selector = cmd[cmd.index("-f") + 1]
    assert "vcodec!^=?vp" in selector and "vcodec!^=?av0" in selector


# ── Сквозной прогон download_media с поддельными утилитами ──────────────

_FAKE_YTDLP = """#!{python}
import pathlib, sys
argv = sys.argv[1:]
log = pathlib.Path({log!r})
with log.open("a") as handle:
    handle.write("ytdlp " + argv[argv.index("-f") + 1] + "\\n")
    if "--load-info-json" in argv:
        handle.write("loaded-info-json\\n")
is_retry = "vcodec!^=?vp" in argv[argv.index("-f") + 1]
print('JWPLAN {{"width": %d, "height": %d}}\t[]' % ((720, 1280) if is_retry else (1080, 1920)), flush=True)
if {retry_fails!r} and is_retry:
    sys.stderr.write("ERROR: boom")
    sys.exit(1)
codec = {retry_codec!r} if is_retry else {codec!r}
for index in range({files_count!r}):
    template = argv[argv.index("-o") + 1].replace("%(playlist_index)s", str(index)).replace("%(ext)s", "mp4")
    if {json_without_media!r} and not is_retry:
        pathlib.Path(template).with_suffix(".info.json").write_text("{{}}")
        sys.stderr.write("ERROR: no formats")
        sys.exit(1)
    pathlib.Path(template).write_text(codec + " " + {duration!r} + " " + {transfer!r})
    if "--write-info-json" in argv:
        pathlib.Path(template).with_suffix(".info.json").write_text("{{}}")
"""

_FAKE_GALLERY_DL = """#!{python}
import pathlib, sys
argv = sys.argv[1:]
merged = "videos=merged" in argv
with pathlib.Path({log!r}).open("a") as handle:
    handle.write("gallery-dl merged\\n" if merged else "gallery-dl\\n")
if merged and {retry_fails!r}:
    sys.exit(1)
name = argv[argv.index("-f") + 1].replace("{{num}}", "1").replace("{{id|filename|num}}", "x").replace("{{extension}}", "mp4")
codec = {retry_codec!r} if merged else {codec!r}
(pathlib.Path(argv[argv.index("-D") + 1]) / name).write_text(codec + " " + {duration!r} + " " + {transfer!r})
"""

_FAKE_FFPROBE = """#!{python}
import json, pathlib, sys
with pathlib.Path({log!r}).open("a") as handle:
    handle.write("ffprobe\\n")
parts = pathlib.Path(sys.argv[-1]).read_text().split()
codec, duration = parts[:2]
transfer = parts[2] if len(parts) > 2 else "unknown"
if codec == "broken":
    sys.exit(1)
print(json.dumps({{"streams": [
    {{"codec_type": "video", "codec_name": codec, "width": 1080, "height": 1920, "color_transfer": transfer}},
    {{"codec_type": "audio", "codec_name": "aac"}}],
  "format": {{"duration": duration}}}}))
"""

_FAKE_FFMPEG = """#!{python}
import pathlib, sys, time
argv = sys.argv[1:]
with pathlib.Path({log!r}).open("a") as handle:
    handle.write("ffmpeg\\n")
time.sleep({ffmpeg_sleep!r})
with pathlib.Path({log!r}).open("a") as handle:
    handle.write("ffmpeg_end\\n")
if {fail!r} or ({fail_on_tonemap!r} and "zscale" in argv[argv.index("-vf") + 1]):
    sys.stderr.write("boom")
    sys.exit(1)
source = pathlib.Path(argv[argv.index("-i") + 1])
duration = source.read_text().split()[1]
pathlib.Path(argv[-1]).write_text("h264 " + duration + " unknown")
"""


def _install_tools(
    tmp_path, monkeypatch, *, codec: str, duration: str, ffmpeg_fails: bool = False,
    retry_codec: str = "h264", files_count: int = 1, ffmpeg_sleep: float = 0.0, retry_fails: bool = False,
    transfer: str = "unknown", json_without_media: bool = False, fail_on_tonemap: bool = False,
):
    bin_dir, dl_dir = tmp_path / "bin", tmp_path / "downloads"
    bin_dir.mkdir()
    dl_dir.mkdir()
    log = tmp_path / "calls.log"
    scripts = {
        "yt-dlp": _FAKE_YTDLP.format(
            python=sys.executable, log=str(log), codec=codec, duration=duration,
            retry_codec=retry_codec, files_count=files_count, retry_fails=retry_fails,
            transfer=transfer, json_without_media=json_without_media,
        ),
        "gallery-dl": _FAKE_GALLERY_DL.format(
            python=sys.executable, log=str(log), codec=codec, duration=duration, retry_codec=retry_codec,
            retry_fails=retry_fails, transfer=transfer,
        ),
        "ffprobe": _FAKE_FFPROBE.format(python=sys.executable, log=str(log)),
        "ffmpeg": _FAKE_FFMPEG.format(python=sys.executable, log=str(log), fail=ffmpeg_fails, ffmpeg_sleep=ffmpeg_sleep,
            fail_on_tonemap=fail_on_tonemap,
        ),
    }
    for name, body in scripts.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    return dl_dir, log


def _calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


async def test_vp9_instagram_video_is_transcoded_to_h264(tmp_path, monkeypatch):
    dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4")
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert _calls(log).count("ffmpeg") == 1
    assert Path(result.file_path).read_text().startswith("h264")
    assert [p.name for p in dl_dir.iterdir()] == [Path(result.file_path).name]


async def test_h264_instagram_video_is_not_reencoded(tmp_path, monkeypatch):
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="h264", duration="8.4")
    result = await download_media(_URL, "instagram")
    assert result.success is True
    assert "ffmpeg" not in _calls(log)


async def test_long_vp9_instagram_video_is_redownloaded_as_h264(tmp_path, monkeypatch):
    long_duration = str(TRANSCODE_MAX_DURATION_SEC + 60)
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration=long_duration)
    result = await download_media(_URL, "instagram")
    calls = _calls(log)
    assert result.success is True
    assert len([c for c in calls if c.startswith("ytdlp")]) == 2
    assert "vcodec!^=?vp" in [c for c in calls if c.startswith("ytdlp")][-1]
    assert "ffmpeg" not in calls
    assert Path(result.file_path).read_text().startswith("h264")


async def test_other_platforms_are_not_transcoded_and_probed_once_for_the_log(tmp_path, monkeypatch):
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4")
    result = await download_media("https://www.facebook.com/watch?v=1", "facebook")
    assert result.success is True
    assert "ffmpeg" not in _calls(log)
    assert _calls(log).count("ffprobe") == 1


async def test_transcode_failure_is_a_failed_download_without_leftovers(tmp_path, monkeypatch):
    dl_dir, _log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4", ffmpeg_fails=True)
    result = await download_media(_URL, "instagram")
    assert result.success is False
    assert result.error_message
    assert list(dl_dir.iterdir()) == []


async def test_transcode_to_h264_raises_and_removes_temp_on_failure(tmp_path, monkeypatch):
    dl_dir, _log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4", ffmpeg_fails=True)
    source = dl_dir / "abc.mp4"
    source.write_text("vp9 8.4")
    with pytest.raises(TranscodeError):
        await video_transcode.transcode_to_h264(source, _info("vp9"))
    assert [p.name for p in dl_dir.iterdir()] == ["abc.mp4"]


async def test_download_complete_log_reports_codec_and_height(tmp_path, monkeypatch):
    _install_tools(tmp_path, monkeypatch, codec="h264", duration="8.4")
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="INFO")
    try:
        await download_media(_URL, "instagram")
    finally:
        logger.remove(sink_id)
    complete = [m for m in messages if m.startswith("Download complete")]
    assert complete and "codec=h264" in complete[0] and "height=1920" in complete[0]


async def test_long_post_via_gallery_dl_is_refetched_with_merged_videos(tmp_path, monkeypatch):
    long_duration = str(TRANSCODE_MAX_DURATION_SEC + 60)
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration=long_duration)
    result = await download_media("https://www.instagram.com/p/DBc1abc/", "instagram")
    calls = _calls(log)
    assert result.success is True
    assert [c for c in calls if c.startswith("gallery-dl")] == ["gallery-dl", "gallery-dl merged"]
    assert Path(result.file_path).read_text().startswith("h264")


async def test_refetch_that_is_still_non_h264_is_sent_as_is_without_ffmpeg(tmp_path, monkeypatch):
    long_duration = str(TRANSCODE_MAX_DURATION_SEC + 60)
    _dl_dir, log = _install_tools(
        tmp_path, monkeypatch, codec="vp9", duration=long_duration, retry_codec="vp9",
    )
    result = await download_media(_URL, "instagram")
    calls = _calls(log)
    assert result.success is True
    assert len([c for c in calls if c.startswith("ytdlp")]) == 2
    assert "ffmpeg" not in calls
    assert Path(result.file_path).read_text().startswith("vp9")


async def test_carousel_is_judged_by_total_duration_not_per_file(tmp_path, monkeypatch):
    each = str(TRANSCODE_MAX_DURATION_SEC // 2 + 10)
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration=each, files_count=2)
    result = await download_media(_URL, "instagram")
    calls = _calls(log)
    assert result.success is True
    assert len([c for c in calls if c.startswith("ytdlp")]) == 2
    assert "ffmpeg" not in calls


async def test_two_transcodes_never_run_at_the_same_time(tmp_path, monkeypatch):
    dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4", ffmpeg_sleep=0.3)
    sources = []
    for name in ("a.mp4", "b.mp4"):
        (dl_dir / name).write_text("vp9 8.4")
        sources.append(dl_dir / name)
    await asyncio.gather(*(video_transcode.transcode_to_h264(path, _info("vp9")) for path in sources))
    assert _calls(log) == ["ffmpeg", "ffmpeg_end", "ffmpeg", "ffmpeg_end"]


# ── HDR → SDR ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("transfer", ["smpte2084", "arib-std-b67"])
def test_hdr_transfer_is_detected(transfer):
    assert is_hdr(_info("vp9", color_transfer=transfer)) is True


@pytest.mark.parametrize("transfer", ["", "bt709", "unknown"])
def test_sdr_transfer_is_not_hdr(transfer):
    assert is_hdr(_info("vp9", color_transfer=transfer)) is False


def test_hdr_cmd_tonemaps_to_bt709_and_sdr_cmd_does_not():
    hdr_cmd = build_transcode_cmd(Path("in.mp4"), Path("out.mp4"), True, hdr_transfer="smpte2084")
    plain_cmd = build_transcode_cmd(Path("in.mp4"), Path("out.mp4"), True)
    hdr_filter = hdr_cmd[hdr_cmd.index("-vf") + 1]
    assert "color_trc=smpte2084" in hdr_filter and "tonemap" in hdr_filter and "zscale" in hdr_filter
    assert "-colorspace" in hdr_cmd
    assert "tonemap" not in plain_cmd[plain_cmd.index("-vf") + 1]
    assert "-colorspace" not in plain_cmd


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ffmpeg с zscale и tonemap")
async def test_real_ffmpeg_turns_hdr_clip_into_bt709_yuv420p(tmp_path):
    source = tmp_path / "hdr.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=1,format=yuv420p10le",
         "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libvpx-vp9", "-profile:v", "2", "-pix_fmt", "yuv420p10le",
         "-c:a", "aac", str(source)],
        check=True,
    )
    info = _info("vp9", color_transfer="smpte2084")
    result = await video_transcode.transcode_to_h264(source, info)
    probed = await probe_media(result)
    assert probed.video_codec == "h264"
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=pix_fmt,color_transfer", "-of", "csv=p=0", str(result)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "yuv420p,bt709" in out
