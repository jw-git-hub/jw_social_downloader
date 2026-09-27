"""Тексты статус-сообщения: план формата, проценты скачивания и отправки.

Модуль чистый (без I/O): собирает готовые строки для `bot/handlers/user.py`
из `DownloadStatus`/`FormatPlan` (Задача 1, `bot/services/ytdlp_progress.py`)
и оценки скорости отправки (`bot/services/upload_estimate.py`). Тексты — ровно
из брифа (`.superpowers/sdd/2026-09-27-progress/plan.md`, раздел «3. Тексты»),
их формулировки менять нельзя без ведома владельца.
"""

from __future__ import annotations

import math

from bot.services.ytdlp_progress import DownloadPhase, DownloadStatus, FormatPlan
from bot.utils.text import LINE_MARKER

GIB_DISPLAY_FROM_MB = 1000  # от этого порога размер показываем в ГиБ, а не в МиБ
MIB_PER_GIB = 1024
DECIMAL_SEPARATOR = ","  # десятичная запятая — стиль экосистемы (правило С6)
BAR_CELLS = 10
BAR_FILLED = "▰"
BAR_EMPTY = "▱"
BAR_PERCENT_GAP = "  "  # два пробела между шкалой и процентом — для воздуха
PERCENT = 100
SECONDS_PER_MINUTE = 60
UPLOAD_FRACTION_CAP = 0.95  # дальше — «почти готово», без конкретного процента
_TIER_NAMES = {4320: "8K", 2160: "4K"}

DOWNLOAD_ICON_VERB = "⬇️ Скачиваю видео"
MERGE_ICON_VERB = "🎬 Собираю видео и звук"
UPLOAD_ICON_VERB = "📤 Отправляю в Telegram"

PREPARING_TEXT = (
    "⏳ <b>Готовлю загрузку…</b>\n"
    "Большое видео качается несколько минут — я покажу, сколько осталось."
)
QUEUED_TEXT = (
    "⏳ <b>Жду своей очереди</b>\n"
    "Бот сейчас качает другие ролики — начну, как только освободится место."
)


def format_size(mb: float) -> str:
    """904.1 → «904 МБ», 1500 → «1,5 ГБ» (от 1000 МиБ — в ГБ, запятая — десятичный разделитель)."""
    if mb < GIB_DISPLAY_FROM_MB:
        return f"{max(1, round(mb))} МБ"
    gib_value = f"{mb / MIB_PER_GIB:.1f}".replace(".", DECIMAL_SEPARATOR)
    return f"{gib_value} ГБ"


def quality_short(height: int) -> str:
    """2160 → «4K», 1080 → «1080p» — для пояснения о понижении качества."""
    return _TIER_NAMES.get(height, f"{height}p")


def quality_label(height: int) -> str:
    """2160 → «4K · 2160p», 1080 → «1080p» — для заголовка статуса."""
    tier = _TIER_NAMES.get(height)
    return f"{tier} · {height}p" if tier is not None else f"{height}p"


def progress_bar(fraction: float) -> str:
    """0.52 → «▰▰▰▰▰▱▱▱▱▱» (BAR_CELLS ячеек, дробная часть отбрасывается)."""
    clamped = max(0.0, min(1.0, fraction))
    filled = math.floor(clamped * BAR_CELLS)
    return BAR_FILLED * filled + BAR_EMPTY * (BAR_CELLS - filled)


def format_eta(seconds: float) -> str:
    """70 → «~1 мин», 30 → «меньше минуты»."""
    if seconds < SECONDS_PER_MINUTE:
        return "меньше минуты"
    minutes = max(1, round(seconds / SECONDS_PER_MINUTE))
    return f"~{minutes} мин"


def downgrade_note(plan: FormatPlan | None, limit_mb: int) -> str | None:
    """Пояснение «пришлю не в 4K, а в 1080p», только если причина — размер.

    None, если высоты/размер лучшей ступени неизвестны, если лучшая ступень
    не выше выбранной, или если она всё равно укладывается в лимит — тогда
    выбор ниже максимума был сделан по другой причине (кодек, доступность).
    """
    if plan is None:
        return None
    if plan.chosen_height is None or plan.best_height is None or plan.best_mb is None:
        return None
    if plan.best_height <= plan.chosen_height or plan.best_mb <= limit_mb:
        return None
    return (
        f"ℹ️ В {quality_short(plan.best_height)} ролик весит от {format_size(plan.best_mb)}, "
        f"а бот может отправить файл до {format_size(limit_mb)}, поэтому пришлю лучшее, "
        f"что помещается, — {quality_short(plan.chosen_height)}."
    )


def _title(icon_verb: str, height: int | None, size_mb: float | None) -> str:
    """Заголовок статуса, пропуская неизвестные качество/размер."""
    parts = [icon_verb]
    if height is not None:
        parts.append(quality_label(height))
    if size_mb is not None:
        parts.append(format_size(size_mb))
    return f"<b>{' · '.join(parts)}</b>"


def _append_note(text: str, plan: FormatPlan | None, limit_mb: int) -> str:
    note = downgrade_note(plan, limit_mb)
    return f"{text}\n\n{note}" if note else text


def _download_progress_line(status: DownloadStatus) -> str:
    if status.fraction is not None:
        pct = int(status.fraction * PERCENT)
        line = f"{progress_bar(status.fraction)}{BAR_PERCENT_GAP}{pct}%"
        if status.eta_sec is not None:
            line += f" · осталось {format_eta(status.eta_sec)}"
        return line
    if status.downloaded_mb > 0:
        return f"Скачано {format_size(status.downloaded_mb)}"
    return f"{progress_bar(0.0)}{BAR_PERCENT_GAP}0%"


def download_status_text(status: DownloadStatus | None, limit_mb: int) -> str:
    """Текст статуса на фазах подготовки/скачивания/склейки.

    `None` (ролик через gallery-dl, колбэк ни разу не позвал) и «подготовка
    без плана» — один и тот же текст `PREPARING_TEXT`.
    """
    if status is None or (status.phase is DownloadPhase.PREPARING and status.plan is None):
        return PREPARING_TEXT

    plan = status.plan
    height = plan.chosen_height if plan else None
    size_mb = plan.chosen_mb if plan else None

    if status.phase is DownloadPhase.PREPARING:
        title = _title(DOWNLOAD_ICON_VERB, height, size_mb)
        body = f"{progress_bar(0.0)}{BAR_PERCENT_GAP}0%"
    elif status.phase is DownloadPhase.DOWNLOADING:
        title = _title(DOWNLOAD_ICON_VERB, height, size_mb)
        body = _download_progress_line(status)
    else:  # MERGING
        title = _title(MERGE_ICON_VERB, height, size_mb)
        body = f"Ещё {format_eta(status.eta_sec)}." if status.eta_sec is not None else ""

    text = f"{title}\n{body}" if body else title
    return _append_note(text, plan, limit_mb)


def _upload_progress_line(elapsed_sec: float, expected_sec: float) -> str:
    if elapsed_sec < expected_sec:
        fraction = min(elapsed_sec / expected_sec, UPLOAD_FRACTION_CAP)
        pct = int(fraction * PERCENT)
        remaining = expected_sec - elapsed_sec
        return f"{progress_bar(fraction)}{BAR_PERCENT_GAP}≈{pct}% · осталось {format_eta(remaining)}"
    return f"{progress_bar(UPLOAD_FRACTION_CAP)} почти готово — Telegram принимает файл…"


def upload_status_text(
    plan: FormatPlan | None,
    size_mb: float | None,
    elapsed_sec: float | None,
    expected_sec: float | None,
    limit_mb: int,
) -> str:
    """Текст статуса при отправке в Telegram: полоска — только оценка."""
    height = plan.chosen_height if plan else None
    title = _title(UPLOAD_ICON_VERB, height, size_mb)
    if expected_sec is None or elapsed_sec is None:
        return _append_note(title, plan, limit_mb)
    body = _upload_progress_line(elapsed_sec, expected_sec)
    return _append_note(f"{title}\n{body}", plan, limit_mb)


def limit_line(limit_mb: int) -> str:
    """Строка приветствия про лимит Telegram-бота."""
    return (
        f"Видео присылаю в лучшем качестве, которое помещается в <b>{format_size(limit_mb)}</b>, "
        "— больше Telegram-бот отправить не может."
    )


def limits_block(limit_mb: int) -> str:
    """Блок «Ограничения» для помощи."""
    limit_text = format_size(limit_mb)
    return (
        "<b>Ограничения</b>\n"
        f"{LINE_MARKER}файл — до <b>{limit_text}</b>: больше Telegram-бот отправить не может\n"
        f"{LINE_MARKER}видео приходит в лучшем качестве, которое помещается в этот размер: "
        "длинный ролик может прийти не в 4K, а, например, в 1080p — заранее напишу, в каком\n"
        f"{LINE_MARKER}большой файл качается и отправляется несколько минут — покажу, сколько осталось"
    )
