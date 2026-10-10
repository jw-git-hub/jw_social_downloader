"""Instagram отдаёт лучшее разрешение в VP9, а iPhone-клиент Telegram VP9 не
показывает (картинка стоит на первом кадре). Итоговый видеофайл Instagram с
любым не-h264 кодеком обязан перекодироваться в H.264; h264 — не трогаем;
слишком длинный ролик не перекодируем, а перекачиваем в H.264.

Поддельные yt-dlp/ffprobe/ffmpeg лежат в PATH (приём из test_downloader_progress):
кодек «файла» задаёт его содержимое, обращения к ffmpeg и yt-dlp пишутся в журнал.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from loguru import logger

from bot.services import downloader, video_transcode
from bot.services.downloader import _build_command, download_media
from bot.services.media_probe import MediaInfo
from bot.services.video_transcode import (
    TRANSCODE_MAX_DURATION_SEC,
    TranscodeError,
    build_transcode_cmd,
    needs_h264_transcode,
)

_URL = "https://www.instagram.com/reel/DbWoC_CsNcZ/"


def _info(video_codec: str, duration: float = 8.4, audio_codec: str = "aac") -> MediaInfo:
    return MediaInfo(True, True, duration, 1080, 1920, video_codec, audio_codec)


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
    assert cmd[cmd.index("-S") + 1] == downloader.YOUTUBE_FORMAT_SORT
    assert cmd[cmd.index("-f") + 1] == "bv*+ba/b"


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
template = argv[argv.index("-o") + 1].replace("%(playlist_index)s", "NA").replace("%(ext)s", "mp4")
codec = "h264" if "vcodec!^=?vp" in argv[argv.index("-f") + 1] else {codec!r}
pathlib.Path(template).write_text(codec + " " + {duration!r})
"""

_FAKE_FFPROBE = """#!{python}
import json, pathlib, sys
codec, duration = pathlib.Path(sys.argv[-1]).read_text().split()
print(json.dumps({{"streams": [
    {{"codec_type": "video", "codec_name": codec, "width": 1080, "height": 1920}},
    {{"codec_type": "audio", "codec_name": "aac"}}],
  "format": {{"duration": duration}}}}))
"""

_FAKE_FFMPEG = """#!{python}
import pathlib, sys
argv = sys.argv[1:]
with pathlib.Path({log!r}).open("a") as handle:
    handle.write("ffmpeg\\n")
if {fail!r}:
    sys.stderr.write("boom")
    sys.exit(1)
source = pathlib.Path(argv[argv.index("-i") + 1])
duration = source.read_text().split()[1]
pathlib.Path(argv[-1]).write_text("h264 " + duration)
"""


def _install_tools(tmp_path, monkeypatch, *, codec: str, duration: str, ffmpeg_fails: bool = False):
    bin_dir, dl_dir = tmp_path / "bin", tmp_path / "downloads"
    bin_dir.mkdir()
    dl_dir.mkdir()
    log = tmp_path / "calls.log"
    scripts = {
        "yt-dlp": _FAKE_YTDLP.format(python=sys.executable, log=str(log), codec=codec, duration=duration),
        "ffprobe": _FAKE_FFPROBE.format(python=sys.executable),
        "ffmpeg": _FAKE_FFMPEG.format(python=sys.executable, log=str(log), fail=ffmpeg_fails),
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
    assert "vcodec!^=?vp" in calls[-1]
    assert "ffmpeg" not in calls
    assert Path(result.file_path).read_text().startswith("h264")


async def test_other_platforms_are_not_probed_or_transcoded(tmp_path, monkeypatch):
    _dl_dir, log = _install_tools(tmp_path, monkeypatch, codec="vp9", duration="8.4")
    result = await download_media("https://www.facebook.com/watch?v=1", "facebook")
    assert result.success is True
    assert "ffmpeg" not in _calls(log)


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
