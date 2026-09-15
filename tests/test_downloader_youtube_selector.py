"""H-1: пин клиентов YouTube (player_client=web_safari,android_vr,tv) снят —
живой замер показал, что он ограничивал выдачу до 360p (itag 18), а не давал
предсклеенные HLS avc1 1080p/720p, как утверждал старый комментарий. См.
downloader.py, ветка platform == "youtube" в `_build_command`.

Ревью-раунд (C-1/I-1/I-4): терминальная ветка без ограничения кодека
пропускала av01 (плеер Telegram его не показывает — «звук без картинки»),
а фиксированный аудио-бюджет (5M, потом 15M) рвал каскад по длительности
ролика на любом фиксированном числе. Тесты ниже стерегут оба фикса.
"""

import re
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
    # (MAX_FILE_SIZE_MB - audio_cap, audio_cap = MAX_FILE_SIZE_MB // 5).
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 50)
    selector_50 = _selector(_youtube_cmd(tmp_path))
    assert "filesize_approx<40M" in selector_50
    assert "filesize_approx<10M" in selector_50

    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 100)
    selector_100 = _selector(_youtube_cmd(tmp_path))
    assert "filesize_approx<80M" in selector_100
    assert "filesize_approx<20M" in selector_100
    assert "filesize_approx<40M" not in selector_100


def test_youtube_cascade_has_final_branch_without_size_filter(tmp_path):
    selector = _selector(_youtube_cmd(tmp_path))
    branches = selector.split("/")
    # Последняя ветка (страховка от «пустого» селектора) не должна нести
    # никаких фильтров по размеру — иначе каскад в принципе может вернуть
    # пустой список форматов.
    assert "filesize" not in branches[-1]
    assert "filesize" not in branches[-2]


# ── Ревью C-1: AV1 допустим только самым последним шансом ───────────────


def test_youtube_only_terminal_branch_allows_any_codec(tmp_path):
    selector = _selector(_youtube_cmd(tmp_path))
    branches = selector.split("/")
    # Терминальная ветка — буквально "bv*+ba/b" (два последних элемента
    # после общего split по "/"): она специально не ограничивает кодек,
    # чтобы гарантировать непустой результат.
    terminal, guarded = branches[-2:], branches[:-2]
    assert terminal == ["bv*+ba", "b"]
    # Все ветки ДО терминальной обязаны явно называть кодек (avc1/vp9) —
    # иначе дефолтная сортировка форматов у yt-dlp подставит av01 раньше,
    # чем каскад дойдёт до терминальной ветки, и Telegram получит
    # непроигрываемое видео (та же проблема, что была у Facebook).
    for branch in guarded:
        assert "vcodec~=" in branch, f"ветка без явного кодека: {branch!r}"
        assert "av01" not in branch


def test_youtube_has_unfiltered_avc1_guard_before_terminal(tmp_path):
    # Явный "предохранитель" C-1: ветка avc1+m4a без фильтров размера,
    # которая должна сработать раньше терминальной bv*+ba/b — только если
    # у ролика вообще нет ни одного avc1-трека, каскад дойдёт до av01.
    selector = _selector(_youtube_cmd(tmp_path))
    assert "bv*[vcodec~='^avc1']+ba[ext=m4a]/bv*+ba/b" in selector


# ── Ревью I-1: аудио-бюджет не должен рвать каскад по длительности ──────


def test_youtube_audio_budget_scales_with_settings(tmp_path, monkeypatch):
    # audio_cap — доля MAX_FILE_SIZE_MB, а не магическая константа (5M/15M
    # из предыдущих раундов): сумма video_cap и audio_cap не должна
    # превышать MAX_FILE_SIZE_MB на любом значении настройки, включая
    # будущий локальный Bot API (1500).
    audio_caps = []
    for max_size in (50, 100, 1500):
        monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", max_size)
        selector = _selector(_youtube_cmd(tmp_path))
        caps = sorted({int(n) for n in re.findall(r"filesize_approx<(\d+)M", selector)})
        assert len(caps) == 2, caps
        audio_cap, video_cap = caps
        # Сама по себе эта сумма верна почти "по построению" (video_cap
        # выводится как MAX - audio_cap) — ловит только полностью
        # оторванные от settings числа, поэтому дополнительно ниже
        # проверяем, что САМ audio_cap меняется вместе с MAX_FILE_SIZE_MB
        # (магическая константа 15M не изменилась бы).
        assert video_cap + audio_cap == max_size
        audio_caps.append(audio_cap)

    # audio_cap обязан расти вместе с MAX_FILE_SIZE_MB — если бы это была
    # константа (старые 5M/15M), три значения совпали бы.
    assert len(set(audio_caps)) == 3, audio_caps
    assert audio_caps[0] < audio_caps[1] < audio_caps[2]


def test_youtube_has_worst_audio_fallback_without_size_filter(tmp_path):
    # `wa` (worst audio) — самый лёгкий доступный трек по определению
    # yt-dlp, фильтр размера ему не нужен. Эта ветка обязана существовать
    # ДО vp9-фолбэка и до терминальной ветки, иначе длинные ролики (аудио
    # тяжелее бюджета при коротком видео) будут падать в av01/terminal —
    # именно так был найден I-1 (F6TRAYUUDcQ, 42 мин).
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
