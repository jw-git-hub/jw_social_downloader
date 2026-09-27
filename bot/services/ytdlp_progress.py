"""Разбор служебных строк yt-dlp: план формата и прогресс скачивания/склейки.

Модуль чистый (без I/O) — он только разбирает строки stdout yt-dlp,
накопленные `bot/services/process_stream.py`, и превращает их в состояние
для показа пользователю (`bot/services/progress_texts.py`, Задача 2).

Почему `--no-quiet`: `--print` неявно включает `--quiet` (yt-dlp,
`__init__.py:760`), а в quiet-режиме yt-dlp не печатает строку
«[download] File is larger than max-filesize (…). Aborting.» — по ней
правило `too_large` в `_parse_error` (`bot/services/downloader.py`) узнаёт
ошибку «файл слишком большой». Без `--no-quiet` эта классификация тихо
ломается на любой платформе.

Почему `before_dl`, а не `video` или отдельный `-J`: `--print before_dl:…`
срабатывает уже ПОСЛЕ выбора формата и ДО первого байта, в том же процессе
(без второго запроса к платформе) и без включения `simulate` — живой прогон
2026-09-27 подтвердил, что ролик после этого действительно скачивается.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

MIB = 1024 * 1024
PLAN_PREFIX = "JWPLAN "
PROGRESS_PREFIX = "JWPROG|"
POSTPROCESS_PREFIX = "JWPP|"
PLAN_TEMPLATE = (
    "JWPLAN %(.{width,height,filesize,filesize_approx})j"
    "\t%(formats.:.{width,height,vcodec,filesize,filesize_approx})j"
)
PROGRESS_TEMPLATE = (
    "JWPROG|%(info.format_id)s|%(progress.status)s|%(progress.downloaded_bytes)s"
    "|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s"
)
POSTPROCESS_TEMPLATE = "JWPP|%(progress.postprocessor)s|%(progress.status)s"
PROGRESS_DELTA_SEC = "1"  # не чаще строки в секунду: 157 строк на 155 с 4K вместо тысяч
YTDLP_PROGRESS_ARGS = (
    "--no-quiet", "--newline", "--progress-delta", PROGRESS_DELTA_SEC,
    "--progress-template", "download:" + PROGRESS_TEMPLATE,
    "--progress-template", "postprocess:" + POSTPROCESS_TEMPLATE,
    "--print", "before_dl:" + PLAN_TEMPLATE,
)
FRACTION_CAP = 0.99  # 100 % в фазе скачивания не показываем: впереди склейка
ETA_MIN_ELAPSED_SEC = 5.0  # раньше средняя скорость бессмысленна
MERGE_MIB_PER_SEC = 13.0  # замер 2026-09-27 на RK3399: 904 МиБ склеились за 55–67 с
NON_MERGING_POSTPROCESSORS = frozenset({"MoveFiles"})
# (номинальная высота, порог короткой стороны, порог длинной стороны), по убыванию
QUALITY_TIERS = (
    (4320, 4320, 7680), (2160, 2160, 3840), (1440, 1440, 2560), (1080, 1080, 1920),
    (720, 720, 1280), (480, 480, 854), (360, 360, 640), (240, 240, 426), (144, 144, 256),
)


class DownloadPhase(str, Enum):
    PREPARING = "preparing"  # yt-dlp запущен, прогресса ещё нет
    DOWNLOADING = "downloading"
    MERGING = "merging"  # постобработка ffmpeg (склейка/исправление контейнера)


@dataclass(frozen=True)
class FormatPlan:
    chosen_height: int | None  # номинальная высота выбранного видео (2160, 1080…); None — неизвестна
    chosen_mb: float | None  # ожидаемый размер итогового файла, МиБ
    best_height: int | None  # номинальная высота лучшего видеоформата ролика
    best_mb: float | None  # самый лёгкий известный размер видео на best_height, МиБ


@dataclass(frozen=True)
class DownloadStatus:
    phase: DownloadPhase
    plan: FormatPlan | None = None
    fraction: float | None = None  # 0..FRACTION_CAP; None — общий размер неизвестен
    downloaded_mb: float = 0.0
    eta_sec: float | None = None  # DOWNLOADING — остаток скачивания; MERGING — оценка склейки


def nominal_height(width: float | int | None, height: float | int | None) -> int | None:
    """Номинальная высота «ступени» YouTube по кадру, а не голая высота.

    Правило: самая высокая ступень QUALITY_TIERS, где короткая сторона кадра
    ≥ её порога короткой стороны, либо длинная сторона ≥ её порога длинной
    стороны (кинематографичные 2.4:1 и Shorts 9:16 иначе определялись бы
    неверно). Ниже всех ступеней — короткая сторона как есть.
    """
    if width is None or height is None:
        return None
    try:
        w, h = float(width), float(height)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    short_side, long_side = min(w, h), max(w, h)
    for nominal, short_threshold, long_threshold in QUALITY_TIERS:
        if short_side >= short_threshold or long_side >= long_threshold:
            return nominal
    return int(short_side)


def _known_size(entry: dict) -> float | None:
    size = entry.get("filesize") or entry.get("filesize_approx")
    return float(size) if size else None


def _best_video_stats(formats: list) -> tuple[int | None, float | None]:
    """Лучшая номинальная высота видео и самый лёгкий известный размер на ней.

    Битый список форматов (не list/не словари) даёт (None, None) — план
    верхнего уровня это не портит (см. `_parse_plan`).
    """
    videos = []
    for entry in formats:
        if not isinstance(entry, dict):
            continue
        if entry.get("vcodec") in (None, "none"):
            continue
        height = nominal_height(entry.get("width"), entry.get("height"))
        if height is not None:
            videos.append((height, _known_size(entry)))
    if not videos:
        return None, None
    best_height = max(height for height, _size in videos)
    sizes_at_best = [size for height, size in videos if height == best_height and size is not None]
    best_mb = min(sizes_at_best) / MIB if sizes_at_best else None
    return best_height, best_mb


def _parse_plan(payload: str) -> FormatPlan:
    """Разбирает тело строки JWPLAN (без префикса) в `FormatPlan`.

    Битый верхний JSON бросает исключение выше (план не ставится вовсе —
    так требует контракт `DownloadTracker.feed`). Битый список форматов
    только гасит best_height/best_mb — план всё равно ставится.
    """
    top_json, _sep, formats_json = payload.partition("\t")
    top = json.loads(top_json)
    chosen_height = nominal_height(top.get("width"), top.get("height"))
    chosen_size = _known_size(top)
    chosen_mb = chosen_size / MIB if chosen_size is not None else None

    try:
        formats = json.loads(formats_json) if formats_json else []
        best_height, best_mb = _best_video_stats(formats)
    except (json.JSONDecodeError, TypeError, ValueError):
        best_height, best_mb = None, None

    return FormatPlan(chosen_height, chosen_mb, best_height, best_mb)


def _parse_optional_float(value: str) -> float | None:
    if value in ("", "NA", "None"):
        return None
    return float(value)


def _totals(
    tracks: dict[str, tuple[float, float | None]], plan: FormatPlan | None
) -> tuple[float, float | None]:
    """Сумма скачанного и ожидаемый общий размер по всем дорожкам.

    `expected` — `max(размер из плана, сумма известных total дорожек)`: план
    подстраховывает фрагментные закачки (DASH), где `total_bytes` дорожки
    часто пуст, а `expected` всё равно нужен для процента. `None`, если
    размер не известен ни оттуда, ни оттуда.
    """
    done_bytes = sum(downloaded for downloaded, _total in tracks.values())
    known_totals_sum = sum(total for _downloaded, total in tracks.values() if total is not None)
    plan_mb = plan.chosen_mb if plan else None
    plan_bytes = plan_mb * MIB if plan_mb is not None else 0.0
    expected_bytes = max(plan_bytes, known_totals_sum)
    return done_bytes, expected_bytes if expected_bytes > 0 else None


class DownloadTracker:
    """Состояние одной загрузки yt-dlp, собранное из строк JW*.

    `feed` вызывается для каждой служебной строки stdout (см.
    `bot/services/downloader.py::_status_line_handler`), `snapshot` отдаёт
    текущее состояние для колбэка `on_status`.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._phase = DownloadPhase.PREPARING
        self._plan: FormatPlan | None = None
        self._tracks: dict[str, tuple[float, float | None]] = {}
        self._start_time: float | None = None
        self._done_bytes = 0.0
        self._expected_bytes: float | None = None
        self._fraction: float | None = None
        self._merge_eta: float | None = None

    def feed(self, line: str) -> bool:
        """True, если строка служебная (JWPLAN/JWPROG/JWPP) и должна быть
        вырезана из stdout. Никогда не бросает исключений: битая служебная
        строка возвращается как «служебная», но состояние не меняется.
        """
        stripped = line.strip()
        for prefix, handler in (
            (PLAN_PREFIX, self._handle_plan),
            (PROGRESS_PREFIX, self._handle_progress),
            (POSTPROCESS_PREFIX, self._handle_postprocess),
        ):
            if not stripped.startswith(prefix):
                continue
            try:
                handler(stripped[len(prefix):])
            except Exception:
                pass  # битая служебная строка: состояние уже не тронуто (см. ниже)
            return True
        return False

    def _handle_plan(self, payload: str) -> None:
        # _parse_plan строит план ЦЕЛИКОМ в локальной переменной и бросает
        # исключение до присваивания self._plan — если верхний JSON битый,
        # `feed` перехватит исключение снаружи, и состояние останется прежним.
        plan = _parse_plan(payload)
        self._plan = plan
        # Новый план — это следующий ролик плейлиста: старые дорожки и
        # процент к нему не относятся.
        self._tracks = {}
        self._start_time = None
        self._done_bytes = 0.0
        self._expected_bytes = None
        self._fraction = None
        self._phase = DownloadPhase.PREPARING

    def _handle_progress(self, payload: str) -> None:
        track_id, _status, downloaded_s, total_s, estimate_s = payload.split("|")
        downloaded = _parse_optional_float(downloaded_s) or 0.0
        total = _parse_optional_float(total_s) or _parse_optional_float(estimate_s)

        tracks = dict(self._tracks)
        tracks[track_id] = (downloaded, total)
        done_bytes, expected_bytes = _totals(tracks, self._plan)

        self._tracks = tracks
        # `or` тут был бы багом: часы на t=0 дают falsy 0.0, и первая строка
        # прогресса всегда «переоткрывала» бы start_time.
        if self._start_time is None:
            self._start_time = self._clock()
        self._done_bytes = done_bytes
        self._expected_bytes = expected_bytes
        self._fraction = _monotonic_fraction(self._fraction, done_bytes, expected_bytes)
        self._phase = DownloadPhase.DOWNLOADING

    def _handle_postprocess(self, payload: str) -> None:
        postprocessor, status = payload.split("|")
        if status != "started" or postprocessor in NON_MERGING_POSTPROCESSORS:
            return
        plan_mb = self._plan.chosen_mb if self._plan else None
        base_mb = plan_mb if plan_mb is not None else self._done_bytes / MIB
        self._phase = DownloadPhase.MERGING
        self._merge_eta = base_mb / MERGE_MIB_PER_SEC

    def snapshot(self) -> DownloadStatus:
        return DownloadStatus(
            phase=self._phase,
            plan=self._plan,
            fraction=self._fraction,
            downloaded_mb=self._done_bytes / MIB,
            eta_sec=self._eta_sec(),
        )

    def _eta_sec(self) -> float | None:
        if self._phase == DownloadPhase.MERGING:
            return self._merge_eta
        if self._phase != DownloadPhase.DOWNLOADING:
            return None
        if self._start_time is None or self._expected_bytes is None or self._done_bytes <= 0:
            return None
        elapsed = self._clock() - self._start_time
        if elapsed < ETA_MIN_ELAPSED_SEC:
            return None
        rate = self._done_bytes / elapsed
        return (self._expected_bytes - self._done_bytes) / rate if rate > 0 else None


def _monotonic_fraction(previous: float | None, done_bytes: float, expected_bytes: float | None) -> float | None:
    """Процент никогда не уменьшается: новая дорожка может временно снизить
    сырое отношение done/expected (см. докстринг `DownloadTracker._handle_progress`).
    """
    if expected_bytes is None:
        return previous
    raw = min(FRACTION_CAP, done_bytes / expected_bytes)
    return raw if previous is None else max(previous, raw)
