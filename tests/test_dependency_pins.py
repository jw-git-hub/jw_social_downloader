import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _requirements() -> str:
    return (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_gallery_dl_is_pinned_to_the_version_that_fixes_instagram():
    # 1.32.11 чинит падение TikTok, 1.32.12 — Instagram posts/reels.
    assert "gallery-dl==1.32.12" in _requirements()


def test_aiogram_is_pinned_exactly():
    assert "aiogram==3.31.0" in _requirements()


def test_ytdlp_keeps_the_curl_cffi_extra():
    # curl_cffi нужен для TikTok и Instagram; без extras он не приедет.
    assert re.search(r"yt-dlp\[[^\]]*curl-cffi[^\]]*\]==", _requirements())


def test_dockerfile_does_not_upgrade_past_the_pins():
    """H-11: `pip install --upgrade` в отдельном RUN обходил пины и кэшировался."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--upgrade" not in dockerfile


def test_installed_versions_match_the_pins():
    """Тест гоняется внутри образа, поэтому проверяет фактически
    установленное, а не только текст файла."""
    from importlib.metadata import version

    assert version("gallery-dl") == "1.32.12"
    assert version("aiogram") == "3.31.0"
