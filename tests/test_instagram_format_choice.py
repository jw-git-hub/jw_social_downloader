"""Поведенческие тесты выбора формата Instagram: реальный движок yt-dlp
(ровно тот argv, что строит `_build_command`) на синтетических форматах, форма
которых снята с живого рилса DbWoC_CsNcZ 2026-10-10: DASH-видео только VP9 с
`fps`, прогрессивные форматы (H.264+AAC) без `vcodec` и без `fps`, отдельная
DASH-аудиодорожка. Сеть не нужна.
"""

from pathlib import Path

import yt_dlp

from bot.services.downloader import _build_command

FIXTURE_URL = "https://www.instagram.com/reel/fixture0001/"
VP9_CODEC = "vp09.00.31.08.00.01.01.01.00"
AVC_CODEC = "avc1.640028"
AUDIO_ID = "dash-audio"


def _dash_video(format_id: str, height: int, vcodec: str = VP9_CODEC) -> dict:
    return {
        "format_id": format_id, "url": f"https://example.invalid/{format_id}", "ext": "mp4",
        "protocol": "https", "vcodec": vcodec, "acodec": "none", "fps": 30,
        "width": height * 9 // 16, "height": height,
    }


def _progressive(format_id: str, height: int) -> dict:
    return {
        "format_id": format_id, "url": f"https://example.invalid/{format_id}", "ext": "mp4",
        "protocol": "https", "width": height * 9 // 16, "height": height,
    }


def _audio() -> dict:
    return {
        "format_id": AUDIO_ID, "url": "https://example.invalid/audio", "ext": "m4a",
        "protocol": "https", "vcodec": "none", "acodec": "mp4a.40.5", "abr": 48,
    }


def _choose(formats: list[dict], tmp_path: Path, h264_only: bool = False) -> str:
    cmd = _build_command(FIXTURE_URL, "instagram", tmp_path / "out.mp4", None, h264_only)
    argv = cmd[1:-1] + ["-q", "--no-warnings"]
    ydl_opts = yt_dlp.parse_options(argv).ydl_opts
    info = {
        "id": "fixture0001", "title": "t", "extractor": "instagram",
        "extractor_key": "Instagram", "webpage_url": FIXTURE_URL, "duration": 8,
        "formats": formats + [_audio()],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.process_ie_result(info, download=False)["format_id"]


def test_takes_best_resolution_even_when_it_is_vp9(tmp_path):
    formats = [_dash_video("vp9-720", 1280), _dash_video("vp9-1080", 1920), _progressive("prog-720", 1280)]
    assert _choose(formats, tmp_path) == f"vp9-1080+{AUDIO_ID}"


def test_progressive_h264_wins_over_vp9_at_equal_resolution(tmp_path):
    # Без этого у ролика, чей лучший DASH — 720p, лишний раз перекодировался бы VP9.
    formats = [_dash_video("vp9-540", 960), _dash_video("vp9-720", 1280), _progressive("prog-720", 1280)]
    assert _choose(formats, tmp_path) == "prog-720"


def test_dash_h264_wins_over_vp9_and_lower_progressive(tmp_path):
    formats = [
        _dash_video("vp9-1080", 1920), _dash_video("avc-1080", 1920, AVC_CODEC), _progressive("prog-720", 1280),
    ]
    assert _choose(formats, tmp_path) == f"avc-1080+{AUDIO_ID}"


def test_h264_only_takes_highest_resolution_among_non_vp9(tmp_path):
    formats = [
        _dash_video("vp9-1080", 1920), _dash_video("avc-1080", 1920, AVC_CODEC), _progressive("prog-720", 1280),
    ]
    assert _choose(formats, tmp_path, h264_only=True) == f"avc-1080+{AUDIO_ID}"


def test_h264_only_falls_back_to_progressive_when_dash_is_only_vp9(tmp_path):
    formats = [_dash_video("vp9-1080", 1920), _progressive("prog-720", 1280)]
    assert _choose(formats, tmp_path, h264_only=True) == "prog-720"
