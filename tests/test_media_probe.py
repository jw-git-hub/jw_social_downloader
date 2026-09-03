import json

import pytest

from bot.services.media_probe import (
    MediaInfo,
    parse_ffprobe_json,
    video_reject_reason,
)


def _ffprobe_output(streams, duration="12.5"):
    return json.dumps({"streams": streams, "format": {"duration": duration}})


def test_parses_normal_video_with_audio():
    raw = _ffprobe_output([
        {"codec_type": "video", "width": 1920, "height": 1080},
        {"codec_type": "audio"},
    ])
    info = parse_ffprobe_json(raw)
    assert info == MediaInfo(
        has_video=True, has_audio=True, duration=12.5, width=1920, height=1080
    )


def test_detects_video_only_dash_fragment():
    raw = _ffprobe_output([{"codec_type": "video", "width": 1280, "height": 720}])
    info = parse_ffprobe_json(raw)
    assert info.has_video is True
    assert info.has_audio is False


def test_duration_falls_back_to_stream_when_format_lacks_it():
    raw = json.dumps({
        "streams": [
            {"codec_type": "video", "width": 640, "height": 480, "duration": "7.0"},
            {"codec_type": "audio"},
        ],
        "format": {},
    })
    assert parse_ffprobe_json(raw).duration == 7.0


def test_returns_none_on_garbage():
    assert parse_ffprobe_json("not json at all") is None
    assert parse_ffprobe_json("") is None


def test_rotated_video_swaps_dimensions():
    # Вертикальные ролики TikTok/Reels часто приходят с матрицей поворота.
    raw = json.dumps({
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [{"rotation": -90}],
            },
            {"codec_type": "audio"},
        ],
        "format": {"duration": "5.0"},
    })
    info = parse_ffprobe_json(raw)
    assert (info.width, info.height) == (1080, 1920)


@pytest.mark.parametrize(
    "info, expected_substring",
    [
        (None, "не удалось прочитать"),
        (MediaInfo(False, True, 5.0, 0, 0), "видеодорожк"),
        (MediaInfo(True, False, 5.0, 640, 480), "звук"),
        (MediaInfo(True, True, 0.0, 640, 480), "нулевая длительность"),
        (MediaInfo(True, True, 5.0, 0, 0), "разрешение"),
    ],
)
def test_reject_reasons(info, expected_substring):
    reason = video_reject_reason(info)
    assert reason is not None
    assert expected_substring in reason.lower()


def test_good_video_is_accepted():
    assert video_reject_reason(MediaInfo(True, True, 5.0, 1920, 1080)) is None
