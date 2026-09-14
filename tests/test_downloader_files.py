from pathlib import Path

from bot.services.downloader import (
    ALLOWED_EXTS,
    ANIMATION_EXTS,
    IMAGE_EXTS,
    VIDEO_EXTS,
    _build_command,
    _find_downloaded_files,
    _media_type_for,
)


def _touch(directory: Path, name: str) -> Path:
    p = directory / name
    p.write_bytes(b"\x00" * 64)
    return p


# ── Белый список расширений (C-2) ────────────────────────────────────────


def test_partial_and_fragment_files_are_not_treated_as_media(tmp_path):
    prefix = "abc123"
    _touch(tmp_path, f"{prefix}.mp4")
    _touch(tmp_path, f"{prefix}.mp4.part")
    _touch(tmp_path, f"{prefix}.ytdl")
    _touch(tmp_path, f"{prefix}.f137.mp4")
    _touch(tmp_path, f"{prefix}.f140.m4a")
    found = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert found == [f"{prefix}.mp4"]


def test_unknown_extensions_are_rejected(tmp_path):
    prefix = "abc123"
    _touch(tmp_path, f"{prefix}.json")
    _touch(tmp_path, f"{prefix}.txt")
    _touch(tmp_path, f"{prefix}.description")
    assert _find_downloaded_files(tmp_path, prefix) == []


def test_other_prefixes_are_never_picked_up(tmp_path):
    _touch(tmp_path, "abc123.mp4")
    _touch(tmp_path, "def456.mp4")
    found = [p.name for p in _find_downloaded_files(tmp_path, "abc123")]
    assert found == ["abc123.mp4"]


def test_allowed_exts_is_the_union_of_the_three_sets():
    assert ALLOWED_EXTS == VIDEO_EXTS | IMAGE_EXTS | ANIMATION_EXTS
    assert ".mp4" in VIDEO_EXTS
    assert ".webm" in VIDEO_EXTS
    assert ".mkv" in VIDEO_EXTS


# ── .gif как анимация (M-17) ─────────────────────────────────────────────


def test_gif_is_no_longer_an_image():
    assert ".gif" not in IMAGE_EXTS
    assert ANIMATION_EXTS == {".gif"}


def test_media_type_has_three_values(tmp_path):
    assert _media_type_for(tmp_path / "a.gif") == "animation"
    assert _media_type_for(tmp_path / "a.jpg") == "image"
    assert _media_type_for(tmp_path / "a.PNG") == "image"
    assert _media_type_for(tmp_path / "a.mp4") == "video"
    assert _media_type_for(tmp_path / "a.webm") == "video"


# ── Контейнер Instagram (M-16) ───────────────────────────────────────────


def test_instagram_pins_output_container_to_mp4(tmp_path):
    cmd = _build_command(
        "https://www.instagram.com/p/DBc1/", "instagram", tmp_path / "out.%(ext)s", None
    )
    assert "--merge-output-format" in cmd
    assert cmd[cmd.index("--merge-output-format") + 1] == "mp4"


def test_every_platform_pins_output_container_to_mp4(tmp_path):
    for platform in ("instagram", "tiktok", "pinterest", "facebook", "youtube"):
        cmd = _build_command(
            "https://example.invalid/x", platform, tmp_path / "out.%(ext)s", None
        )
        assert "--merge-output-format" in cmd, platform
        assert cmd[cmd.index("--merge-output-format") + 1] == "mp4", platform
