"""Поведенческие тесты YouTube-селектора: реальный движок выбора форматов
yt-dlp (ровно тот argv, что строит `_build_command`) прогоняется офлайн на
синтетических форматах — сеть не нужна. Это страховка на будущие апгрейды
yt-dlp: `test_downloader_youtube_selector.py` проверяет ТЕКСТ команды, а этот
файл проверяет её ПОВЕДЕНИЕ — единицы фильтров, имена полей сортировки и
семантику `filesize_approx<...` могут незаметно поменять смысл при апгрейде
yt-dlp, и текстовая проверка это не поймает.

Решение владельца 2026-09-26: потолка разрешения нет вообще («самый лучший
вариант всегда выбираем») — 8K допустим наравне с 4K, если влезает в бюджет.
"""

import functools
import http.server
import threading
from pathlib import Path

import pytest
import yt_dlp

from bot.config import settings
from bot.services.downloader import _build_command

MIB = 1024 * 1024
FIXTURE_URL = "https://www.youtube.com/watch?v=fixture0001"


def _video(format_id: str, height: int, vcodec: str, size_mib: float, *, ext: str = "mp4", hdr: str = "SDR") -> dict:
    return {
        "format_id": format_id,
        "url": f"https://example.invalid/{format_id}",
        "ext": ext,
        "protocol": "https",
        "vcodec": vcodec,
        "acodec": "none",
        "width": height * 16 // 9,
        "height": height,
        "fps": 30,
        "dynamic_range": hdr,
        "filesize": int(size_mib * MIB),
        "filesize_approx": int(size_mib * MIB),
    }


def _audio(format_id: str, acodec: str, ext: str, abr: int, size_mib: float) -> dict:
    return {
        "format_id": format_id,
        "url": f"https://example.invalid/{format_id}",
        "ext": ext,
        "protocol": "https",
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
        "filesize": int(size_mib * MIB),
        "filesize_approx": int(size_mib * MIB),
    }


def _choose(formats: list[dict], tmp_path: Path) -> str:
    cmd = _build_command(FIXTURE_URL, "youtube", tmp_path / "out.mp4", None)
    argv = cmd[1:-1] + ["-q", "--no-warnings"]
    ydl_opts = yt_dlp.parse_options(argv).ydl_opts
    info = {
        "id": "fixture0001",
        "title": "t",
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "webpage_url": FIXTURE_URL,
        "duration": 300,
        "formats": formats,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.process_ie_result(info, download=False)["format_id"]


def _ladder() -> list[dict]:
    return [
        _video("137", 1080, "avc1.640028", 160),
        _video("248", 1080, "vp9", 115, ext="webm"),
        _video("399", 1080, "av01.0.08M.08", 100),
        _video("308", 1440, "vp9", 350, ext="webm"),
        _video("315", 2160, "vp9", 900, ext="webm"),
        _video("401", 2160, "av01.0.12M.08", 800),
    ]


def _audio_tracks() -> list[dict]:
    return [
        _audio("140", "mp4a.40.2", "m4a", 129, 5),
        _audio("139", "mp4a.40.5", "m4a", 49, 2),
        _audio("251", "opus", "webm", 133, 5),
    ]


def test_takes_4k_when_it_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    assert _choose(_ladder() + _audio_tracks(), tmp_path) == "315+140"


def test_resolution_beats_codec_when_vp9_4k_is_too_big(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    formats = [f for f in _ladder() if f["format_id"] != "315"]
    formats.append(_video("315", 2160, "vp9", 1450, ext="webm"))
    assert _choose(formats + _audio_tracks(), tmp_path) == "401+140"


def test_steps_down_to_1440p_when_no_4k_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    formats = [f for f in _ladder() if f["format_id"] not in ("315", "401")]
    formats.append(_video("315", 2160, "vp9", 2000, ext="webm"))
    formats.append(_video("401", 2160, "av01.0.12M.08", 1600))
    assert _choose(formats + _audio_tracks(), tmp_path) == "308+140"


def test_prefers_h264_at_equal_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    formats = [
        _video("137", 1080, "avc1.640028", 160),
        _video("248", 1080, "vp9", 115, ext="webm"),
        _video("399", 1080, "av01.0.08M.08", 100),
    ]
    assert _choose(formats + _audio_tracks(), tmp_path) == "137+140"


def test_prefers_sdr_over_hdr_at_equal_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    formats = [f for f in _ladder() if f["format_id"] not in ("315", "401")]
    formats.append(_video("401", 2160, "av01.0.12M.08", 900, hdr="SDR"))
    formats.append(_video("701", 2160, "av01.0.13M.10", 850, hdr="HDR10"))
    assert _choose(formats + _audio_tracks(), tmp_path) == "401+140"


def test_takes_8k_when_it_fits(tmp_path, monkeypatch):
    # Решение владельца: потолка 4K нет — 8K (AV1, YouTube не отдаёт его ни в
    # каком другом кодеке) выбирается наравне с 4K, если влезает в бюджет.
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    formats = _ladder() + [_video("571", 4320, "av01.0.17M.08", 474)]
    assert _choose(formats + [_audio("140", "mp4a.40.2", "m4a", 129, 5)], tmp_path) == "571+140"


def test_steps_down_from_8k_when_it_does_not_fit(tmp_path, monkeypatch):
    # Тот же набор, но 8K-дорожка слишком тяжёлая для видео-бюджета (1380 из
    # 1500) — ступенька вниз к 2160p, а не отказ.
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    formats = _ladder() + [_video("571", 4320, "av01.0.17M.08", 1450)]
    assert _choose(formats + [_audio("140", "mp4a.40.2", "m4a", 129, 5)], tmp_path) == "315+140"


def test_cloud_limit_keeps_old_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 50)
    formats = _ladder() + [
        _video("136", 720, "avc1.4d401f", 30),
        _video("247", 720, "vp9", 25, ext="webm"),
    ]
    assert _choose(formats + _audio_tracks(), tmp_path) == "136+140"


def test_long_video_falls_back_to_lightest_m4a(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    audio = [
        _audio("140", "mp4a.40.2", "m4a", 129, 300),
        _audio("139", "mp4a.40.5", "m4a", 49, 130),
        _audio("251", "opus", "webm", 133, 290),
    ]
    assert _choose(_ladder() + audio, tmp_path) == "315+139"


def test_video_without_m4a_takes_any_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    audio = [_audio("251", "opus", "webm", 133, 5)]
    assert _choose(_ladder() + audio, tmp_path) == "315+251"


# ── Ревью Important: запасная ветка каскада (branch 3) должна уложить сумму
# в MAX_FILE_SIZE_MB и не подсовывать surround-дорожку вместо AAC ─────────


def test_long_video_total_stays_within_limit(tmp_path, monkeypatch):
    # 10-часовой ролик: обе AAC-дорожки тяжелее бюджета веток 1–2
    # (YOUTUBE_AUDIO_BUDGET_MAX_MB=120 из 1500) — обе ветки отваливаются, в
    # игру вступает запасная ветка (branch 3). Раньше она резервировала под
    # аудио 0 МиБ (видео-фильтр брался тем же video_mb, что и у веток 1–2) —
    # 137+139 = 1330+206 = 1536 МиБ превышал MAX_FILE_SIZE_MB. Теперь ветка 3
    # резервирует под аудио пятую часть лимита на стороне видео.
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    video = [
        _video("137", 1080, "avc1.640028", 1330),
        _video("248", 1080, "vp9", 925, ext="webm"),
        _video("136", 720, "avc1.4d401f", 300),
    ]
    audio = [
        _audio("139", "mp4a.40.5", "m4a", 49, 206),
        _audio("140", "mp4a.40.2", "m4a", 129, 550),
    ]
    formats = {f["format_id"]: f for f in video + audio}
    chosen = _choose(video + audio, tmp_path)
    assert chosen == "248+139"
    total_mib = sum(formats[fid]["filesize"] for fid in chosen.split("+")) / MIB
    assert total_mib <= 1500, total_mib


def test_fallback_audio_skips_surround_tracks(tmp_path, monkeypatch):
    # Облако (лимит 50): все AAC-дорожки тяжелее бюджета веток 1–2
    # (audio_mb=10 из 50) — обе ветки отваливаются, выбирает запасная ветка.
    # `380` — surround E-AC-3 (5.1) в контейнере m4a: раньше проходил через
    # `wa[ext=m4a]` и, из-за сортировки `-S ...,+codec:avc:m4a`, ранжировался
    # НИЖЕ mp4a, поэтому `wa` («самый худший») выбирал именно его — на части
    # Android AC-3 в MP4 воспроизводится без звука. Фильтр `acodec^=mp4a`
    # обязан отсечь его целиком, оставив выбор только между AAC-дорожками.
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 50)
    video = [
        _video("247", 720, "vp9", 36, ext="webm"),
        _video("135", 480, "avc1.4d401e", 19),
    ]
    audio = [
        _audio("139", "mp4a.40.5", "m4a", 49, 13.7),
        _audio("140", "mp4a.40.2", "m4a", 129, 37),
        _audio("380", "ec-3", "m4a", 384, 110),
    ]
    assert _choose(video + audio, tmp_path) == "247+139"


# ── Холодный кэш googlevideo: dashy + параллельные фрагменты (см. докстринг
# _youtube_args в bot/services/downloader.py) ────────────────────────────


def _protocol_video(format_id: str, height: int, vcodec: str, size_mib: float, protocol: str, *, ext: str = "mp4") -> dict:
    size = int(size_mib * MIB)
    fmt = {
        "format_id": format_id,
        "url": f"https://example.invalid/{format_id}",
        "ext": ext,
        "protocol": protocol,
        "vcodec": vcodec,
        "acodec": "none",
        "width": height * 16 // 9,
        "height": height,
        "fps": 60,
        "dynamic_range": "SDR",
        "filesize_approx": size,
    }
    if protocol != "m3u8_native":
        fmt["filesize"] = size
    if protocol == "http_dash_segments":
        fmt["fragments"] = [{"url": f"https://example.invalid/{format_id}?range=0-1"}]
    return fmt


def _protocol_aac(protocol: str) -> dict:
    fmt = _protocol_video("140", 0, "none", 15, protocol, ext="m4a")
    fmt.update(vcodec="none", acodec="mp4a.40.2", abr=129)
    for key in ("width", "height", "fps", "dynamic_range"):
        fmt.pop(key)
    return fmt


def test_dashy_extractor_args_and_concurrency_reach_yt_dlp(tmp_path):
    # Реальная команда (_build_command), без monkeypatch: --extractor-args и
    # --concurrent-fragments должны дойти до yt_dlp.parse_options как есть.
    cmd = _build_command(FIXTURE_URL, "youtube", tmp_path / "out.mp4", None)
    opts = yt_dlp.parse_options(cmd[1:-1] + ["-q", "--no-warnings"]).ydl_opts
    assert opts["concurrent_fragment_downloads"] == 8
    assert opts["extractor_args"] == {"youtube": {"formats": ["dashy"], "skip": ["hls"]}}


@pytest.mark.parametrize("protocol", ["https", "http_dash_segments"])
def test_format_choice_is_the_same_on_https_and_dash_protocol(tmp_path, monkeypatch, protocol):
    # Лестница 97PbTbK8qI8: 1440p vp9 ~1016 МиБ влезает в бюджет (1500 →
    # 1380 МиБ видео) на ОБОИХ протоколах — dashy меняет только протокол
    # закачки уже выбранного формата, не сам выбор.
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    ladder = [
        _protocol_video("299", 1080, "avc1.64002a", 546, protocol),
        _protocol_video("303", 1080, "vp9", 347, protocol, ext="webm"),
        _protocol_video("308", 1440, "vp9", 1016, protocol, ext="webm"),
        _protocol_aac(protocol),
    ]
    assert _choose(ladder, tmp_path) == "308+140"

    too_big = [f for f in ladder if f["format_id"] != "308"]
    too_big.append(_protocol_video("308", 1440, "vp9", 1500, protocol, ext="webm"))
    assert _choose(too_big, tmp_path) == "299+140"


def test_hls_wins_tie_against_dash_which_is_why_skip_hls_is_required(tmp_path, monkeypatch):
    # Документирует, зачем в YOUTUBE_EXTRACTOR_ARGS обязателен skip=hls:
    # -S сортирует протоколы https > m3u8 > dash, и при равных res/fps/кодеке
    # HLS-вариант (размер там всегда лишь оценка ≈, не точный Content-Length)
    # обгоняет DASH. skip=hls решает это на стороне экстрактора — не давая
    # yt-dlp вообще запросить m3u8-плейлист у YouTube, — а не в сортировке.
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1500)
    formats = [
        _protocol_video("299", 1080, "avc1.64002a", 546, "http_dash_segments"),
        _protocol_video("312", 1080, "avc1.64002a", 686, "m3u8_native"),
        _protocol_aac("http_dash_segments"),
    ]
    assert _choose(formats, tmp_path) == "312+140"


@pytest.fixture
def _fragment_server(tmp_path):
    root = tmp_path / "fragment_server_root"
    root.mkdir()
    for index in range(3):
        (root / f"frag{index}").write_bytes(b"\0" * MIB)
    (root / "whole").write_bytes(b"\0" * 3 * MIB)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize("protocol, aborted", [("https", True), ("http_dash_segments", False)])
def test_max_filesize_guards_only_plain_https_not_dash_fragments(_fragment_server, tmp_path, protocol, aborted):
    # Риск фикса (см. докстринг _youtube_args): --max-filesize сверяет
    # Content-Length ОДНОГО ответа. На plain https это весь файл, и превышение
    # обрывает закачку заранее. На http_dash_segments каждый ответ — фрагмент
    # ≤10 МиБ, всегда меньше лимита по отдельности, поэтому --max-filesize не
    # срабатывает; итоговое превышение ловит только гейт oversized_files ПОСЛЕ
    # закачки (не проверяется этим тестом — тест только про сам предохранитель).
    fmt = {
        "format_id": "big", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
        "protocol": protocol, "url": f"{_fragment_server}/whole", "filesize": 3 * MIB,
    }
    if protocol == "http_dash_segments":
        fmt["fragments"] = [{"url": f"{_fragment_server}/frag{i}"} for i in range(3)]
    info = {
        "id": "x", "title": "x", "extractor": "generic", "extractor_key": "Generic",
        "webpage_url": _fragment_server, "formats": [fmt],
    }
    output_path = tmp_path / "out" / "f.mp4"
    opts = yt_dlp.parse_options(
        ["-q", "--no-warnings", "--max-filesize", "1500K", "-o", str(output_path)]
    ).ydl_opts
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.process_ie_result(info, download=True)
    assert output_path.exists() is (not aborted)
