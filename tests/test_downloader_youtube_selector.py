"""YouTube-селектор: максимальное доступное разрешение в ЛЮБОМ кодеке, без
потолка (решение владельца 2026-09-26 — «самый лучший вариант всегда
выбираем», 8K включительно). Совместимость с плеерами обеспечивает не запрет
кодека в `-f`, а порядок сортировки `-S`: при равном разрешении H.264
предпочитается VP9 и AV1, поэтому до 1080p включительно файл остаётся
H.264+AAC, как раньше. Бюджеты размера — в MiB (как `--max-filesize` и гейт
`oversized_files`), аудио-бюджет — доля лимита с потолком в МБ (см.
`YOUTUBE_AUDIO_BUDGET_DIVISOR`/`YOUTUBE_AUDIO_BUDGET_MAX_MB`).

История: пин клиентов YouTube (player_client=web_safari,android_vr,tv) снят —
живой замер показал, что он ограничивал выдачу до 360p (itag 18), а не давал
предсклеенные HLS avc1 1080p/720p, как утверждал старый комментарий.
"""

import re
from pathlib import Path

from bot.config import settings
from bot.services.downloader import (
    YOUTUBE_AUDIO_BUDGET_DIVISOR,
    YOUTUBE_AUDIO_BUDGET_MAX_MB,
    YOUTUBE_CONCURRENT_FRAGMENTS,
    YOUTUBE_EXTRACTOR_ARGS,
    YOUTUBE_FORMAT_SORT,
    _build_command,
)


def _youtube_cmd(tmp_path: Path, url: str = "https://www.youtube.com/watch?v=aaaaaaaaaaa") -> list[str]:
    return _build_command(url, "youtube", tmp_path / "out.%(ext)s", None)


def _selector(cmd: list[str]) -> str:
    return cmd[cmd.index("-f") + 1]


def _sort(cmd: list[str]) -> str:
    return cmd[cmd.index("-S") + 1]


def test_youtube_client_pin_is_removed(tmp_path):
    cmd = _youtube_cmd(tmp_path)
    joined = " ".join(cmd)
    assert "player_client" not in joined
    assert "web_safari" not in joined
    assert "android_vr" not in joined


def test_youtube_cascade_starts_with_bv_plus_ba(tmp_path):
    selector = _selector(_youtube_cmd(tmp_path))
    first_branch = selector.split("/")[0]
    assert first_branch.startswith("bv*")
    assert "+ba" in first_branch


def test_youtube_size_filter_derived_from_settings(tmp_path, monkeypatch):
    # Видео-часть лимита обязана меняться вслед за MAX_FILE_SIZE_MB, единицы —
    # MiB (двоичные мегабайты), как у --max-filesize и гейта oversized_files.
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 50)
    selector_50 = _selector(_youtube_cmd(tmp_path))
    assert "filesize_approx<40MiB" in selector_50
    assert "filesize_approx<10MiB" in selector_50

    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 100)
    selector_100 = _selector(_youtube_cmd(tmp_path))
    assert "filesize_approx<80MiB" in selector_100
    assert "filesize_approx<20MiB" in selector_100
    assert "filesize_approx<40MiB" not in selector_100


def test_youtube_cascade_has_final_branch_without_size_filter(tmp_path):
    selector = _selector(_youtube_cmd(tmp_path))
    branches = selector.split("/")
    # Последняя ветка (страховка от «пустого» селектора) не должна нести
    # никаких фильтров по размеру — иначе каскад в принципе может вернуть
    # пустой список форматов.
    assert "filesize" not in branches[-1]
    assert "filesize" not in branches[-2]


# ── Ревью I-1: аудио-бюджет не должен рвать каскад по длительности ──────


def test_youtube_audio_budget_is_capped_share(tmp_path, monkeypatch):
    # audio_cap — доля MAX_FILE_SIZE_MB (YOUTUBE_AUDIO_BUDGET_DIVISOR), но не
    # больше YOUTUBE_AUDIO_BUDGET_MAX_MB — иначе на 1500 видео теряет сотни
    # МиБ бюджета впустую (владелец решил: разрешение важнее резерва аудио).
    #
    # Запасная ветка каскада (branch 3) считает видео-бюджет иначе:
    # fallback_cap = MAX - MAX // YOUTUBE_AUDIO_BUDGET_DIVISOR, БЕЗ потолка
    # YOUTUBE_AUDIO_BUDGET_MAX_MB. Пока потолок не сработал (50, 100),
    # fallback_cap совпадает с video_cap веток 1–2 — в селекторе только два
    # различных числа. Как только потолок срезает audio_cap (1500, 2000),
    # fallback_cap расходится с video_cap — появляется третье число.
    expected_audio = {50: 10, 100: 20, 1500: 120, 2000: 120}
    for max_size in (50, 100, 1500, 2000):
        monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", max_size)
        selector = _selector(_youtube_cmd(tmp_path))
        caps = sorted({int(n) for n in re.findall(r"filesize_approx<(\d+)MiB", selector)})
        audio_cap = min(max_size // YOUTUBE_AUDIO_BUDGET_DIVISOR, YOUTUBE_AUDIO_BUDGET_MAX_MB)
        video_cap = max_size - audio_cap
        fallback_cap = max_size - max_size // YOUTUBE_AUDIO_BUDGET_DIVISOR
        expected_caps = sorted({audio_cap, video_cap, fallback_cap})
        assert caps == expected_caps, (max_size, caps, expected_caps)
        assert audio_cap == expected_audio[max_size], (max_size, audio_cap)


def test_youtube_has_worst_audio_fallback_without_size_filter(tmp_path):
    # `wa` (worst audio) — самый лёгкий доступный трек по определению
    # yt-dlp, фильтр размера ему не нужен. Эта ветка обязана существовать
    # ДО терминальной, иначе длинные ролики (аудио тяжелее бюджета при
    # коротком видео) будут падать в терминальную ветку без разбора.
    selector = _selector(_youtube_cmd(tmp_path))
    branches = selector.split("/")
    wa_branches = [b for b in branches if b.startswith("wa") or "+wa" in b]
    assert wa_branches, selector
    for branch in wa_branches:
        # audio-часть "wa[...]" не должна нести filesize_approx — иначе
        # это снова магическое число, ломающее самый смысл ветки.
        audio_part = branch.split("+", 1)[1]
        assert audio_part.startswith("wa")
        assert "filesize_approx" not in audio_part


def test_youtube_size_aware_ba_branches_filter_audio_by_size(tmp_path):
    # Ветки, которые используют полноценный `ba[...]` (а не `wa`) ВМЕСТЕ
    # с фильтром размера на видео, обязаны фильтровать по размеру и
    # аудио-часть — иначе это снова I-1: тяжёлая аудио-дорожка на длинном
    # ролике незаметно проходит без всякого бюджета.
    selector = _selector(_youtube_cmd(tmp_path))
    branches = selector.split("/")
    ba_size_aware = [
        b for b in branches
        if "+ba[" in b and "filesize_approx<" in b.split("+ba[", 1)[0]
    ]
    assert len(ba_size_aware) >= 2, selector
    for branch in ba_size_aware:
        audio_part = branch.split("+ba", 1)[1]
        assert "filesize_approx<" in audio_part, f"аудио без фильтра размера: {branch!r}"


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


# ── Решение владельца 2026-09-26: любой кодек, единицы MiB, сортировка ──


def test_youtube_selector_does_not_restrict_codec(tmp_path):
    # Кодековых фильтров в -f больше нет вообще: совместимость обеспечивает
    # порядок -S, а не запрет vp9/av01 в селекторе.
    selector = _selector(_youtube_cmd(tmp_path))
    assert "vcodec" not in selector


def test_youtube_size_filters_are_in_mib(tmp_path):
    selector = _selector(_youtube_cmd(tmp_path))
    units = re.findall(r"filesize_approx<\d+([A-Za-z]+)", selector)
    assert units, selector
    assert all(unit == "MiB" for unit in units), units


def test_youtube_sort_prefers_resolution_without_cap(tmp_path):
    # Проверяем именно ОТСУТСТВИЕ потолка 4K (решение владельца — «самый
    # лучший вариант всегда выбираем»): первое поле сортировки — ровно "res"
    # (без ":2160"), 8K тоже допустим.
    cmd = _youtube_cmd(tmp_path)
    sort_value = _sort(cmd)
    assert sort_value == YOUTUBE_FORMAT_SORT
    fields = sort_value.split(",")
    assert fields[0] == "res"
    assert "hdr:sdr" in fields
    assert "+codec:avc:m4a" in fields
    assert fields.index("hdr:sdr") < fields.index("+codec:avc:m4a")


# ── Холодный кэш googlevideo: dashy + параллельные фрагменты ────────────


def test_youtube_extractor_args_use_dashy_and_skip_hls(tmp_path):
    # Холодный узел googlevideo отдаёт непрогретый топ-формат одним https-
    # соединением на 0.2–1.6 МиБ/с — dashy переключает его на
    # http_dash_segments, качаемые диапазонами параллельно. skip=hls нужен,
    # иначе HLS-вариант с завышенной оценкой размера обгоняет DASH в -S.
    cmd = _youtube_cmd(tmp_path)
    assert cmd[cmd.index("--extractor-args") + 1] == YOUTUBE_EXTRACTOR_ARGS
    assert YOUTUBE_EXTRACTOR_ARGS == "youtube:formats=dashy;skip=hls"


def test_youtube_concurrent_fragments_matches_constant(tmp_path):
    cmd = _youtube_cmd(tmp_path)
    assert cmd[cmd.index("--concurrent-fragments") + 1] == str(YOUTUBE_CONCURRENT_FRAGMENTS)
    assert YOUTUBE_CONCURRENT_FRAGMENTS == 8


def test_youtube_extractor_args_appears_exactly_once(tmp_path):
    # yt-dlp берёт для одного экстрактора ПОСЛЕДНЕЕ значение --extractor-args:
    # если появится второй youtube-специфичный флаг, его надо будет слить в
    # эту же строку через ";", а не добавлять отдельным вхождением.
    cmd = _youtube_cmd(tmp_path)
    assert cmd.count("--extractor-args") == 1
