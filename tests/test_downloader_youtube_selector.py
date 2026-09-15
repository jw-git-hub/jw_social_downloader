"""H-1: пин клиентов YouTube (player_client=web_safari,android_vr,tv) снят —
живой замер показал, что он ограничивал выдачу до 360p (itag 18), а не давал
предсклеенные HLS avc1 1080p/720p, как утверждал старый комментарий. См.
downloader.py, ветка platform == "youtube" в `_build_command`.
"""

from pathlib import Path

from bot.config import settings
from bot.services.downloader import _build_command


def _youtube_cmd(tmp_path: Path, url: str = "https://www.youtube.com/watch?v=aaaaaaaaaaa") -> list[str]:
    return _build_command(url, "youtube", tmp_path / "out.%(ext)s", None)


def _selector(cmd: list[str]) -> str:
    return cmd[cmd.index("-f") + 1]


def test_youtube_client_pin_is_removed(tmp_path):
    cmd = _youtube_cmd(tmp_path)
    joined = " ".join(cmd)
    assert "player_client" not in joined
    assert "web_safari" not in joined
    assert "android_vr" not in joined
    # --extractor-args вообще не нужен без пина.
    assert "--extractor-args" not in cmd


def test_youtube_cascade_starts_with_bv_plus_ba(tmp_path):
    selector = _selector(_youtube_cmd(tmp_path))
    first_branch = selector.split("/")[0]
    assert first_branch.startswith("bv*")
    assert "+ba" in first_branch


def test_youtube_size_filter_derived_from_settings(tmp_path, monkeypatch):
    # Видео-часть лимита обязана меняться вслед за MAX_FILE_SIZE_MB (а не
    # быть захардкожена на старые константы 48/35, как в прежнем
    # селекторе) — проверяем на двух разных значениях настройки, что
    # числа в селекторе действительно разные и соответствуют формуле
    # (MAX_FILE_SIZE_MB - запас на аудио).
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 50)
    selector_50 = _selector(_youtube_cmd(tmp_path))
    assert "filesize_approx<35M" in selector_50

    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 100)
    selector_100 = _selector(_youtube_cmd(tmp_path))
    assert "filesize_approx<85M" in selector_100
    assert "filesize_approx<35M" not in selector_100


def test_youtube_cascade_has_final_branch_without_size_filter(tmp_path):
    selector = _selector(_youtube_cmd(tmp_path))
    branches = selector.split("/")
    # Последняя ветка (страховка от «пустого» селектора) не должна нести
    # никаких фильтров по размеру — иначе каскад в принципе может вернуть
    # пустой список форматов.
    assert "filesize" not in branches[-1]
    assert "filesize" not in branches[-2]


def test_youtube_merge_output_format_still_mp4(tmp_path):
    cmd = _youtube_cmd(tmp_path)
    assert cmd[cmd.index("--merge-output-format") + 1] == "mp4"


def test_youtube_keeps_manifest_filesize_approx_compat_option(tmp_path):
    # Решение: оставить. Флаг влияет только на HLS-форматы (переставляет
    # пометку размера `~` на `≈`, чтобы она матчилась фильтром
    # filesize_approx<...); у DASH-форматов, на которые теперь рассчитан
    # каскад, поле заполняется и без него — флаг расширяет множество
    # кандидатов, а не сужает его, поэтому вреда от него нет.
    cmd = _youtube_cmd(tmp_path)
    assert cmd[cmd.index("--compat-options") + 1] == "manifest-filesize-approx"


def test_youtube_no_playlist_still_set(tmp_path):
    cmd = _youtube_cmd(tmp_path)
    assert "--no-playlist" in cmd
