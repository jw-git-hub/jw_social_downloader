"""Тексты очереди загрузок: статус ждущей ссылки, отказы, уведомление о рестарте.

Модуль чистый (без I/O). Тексты — ровно из брифа
(`.superpowers/sdd/2026-09-27-queue/plan.md`, раздел «3. Тексты»), их
формулировки менять нельзя без ведома владельца.
"""

from __future__ import annotations

from bot.services.download_queue import Admission
from bot.services.progress_texts import PREPARING_TEXT
from bot.utils.text import esc

# Строка списка помещается в экран телефона без переноса на большинстве
# устройств.
LINK_DISPLAY_MAX_CHARS = 60
# Дальше список ссылок сворачивается в «…и ещё K» — иначе отказ с десятками
# ссылок сам становится флудом.
LINK_LIST_LIMIT = 10

REFUSAL_REASONS: dict[Admission, str] = {
    Admission.DUPLICATE: "уже в очереди",
    Admission.FULL: "очередь заполнена",
}


def _display_link(url: str) -> str:
    """Срезать схему и `www.`, обрезать длину, экранировать для HTML."""
    display = url
    for scheme in ("https://", "http://"):
        if display.startswith(scheme):
            display = display[len(scheme):]
            break
    if display.startswith("www."):
        display = display[len("www."):]
    if len(display) > LINK_DISPLAY_MAX_CHARS:
        display = display[: LINK_DISPLAY_MAX_CHARS - 1] + "…"
    return esc(display)


def _bulleted_list_with_tail(lines: list[str]) -> str:
    """Не длиннее `LINK_LIST_LIMIT` строк, а дальше — «…и ещё K»."""
    if len(lines) <= LINK_LIST_LIMIT:
        return "\n".join(lines)
    hidden = len(lines) - LINK_LIST_LIMIT
    shown = "\n".join(lines[:LINK_LIST_LIMIT])
    return f"{shown}\n…и ещё {hidden}"


def queue_status_text(waiting_ahead: int) -> str:
    """Статус ждущей ссылки. При 0 (голова линии) — прежний `PREPARING_TEXT`."""
    if waiting_ahead == 0:
        return PREPARING_TEXT
    return (
        f"⏳ <b>В очереди: {waiting_ahead}-я</b>\n"
        "Качаю твои ссылки по одной. До этой дойду сам — присылать заново не нужно."
    )


def refusal_text(refused: list[tuple[str, Admission]], max_links: int) -> str:
    """Одно сообщение на все отказы одного входящего сообщения."""
    lines = [f"• {_display_link(url)} — {REFUSAL_REASONS[reason]}" for url, reason in refused]
    text = f"⚠️ <b>Не поставил в очередь:</b>\n{_bulleted_list_with_tail(lines)}"

    if any(reason is Admission.FULL for _, reason in refused):
        text += (
            f"\n\nВ очереди может быть не больше {max_links} ссылок. "
            "Когда текущие скачаются, пришли остальные ещё раз."
        )
    return text


def interrupted_text(urls: list[str], refunded: bool) -> str:
    """Одно сообщение на чат после рестарта бота."""
    lines = "\n".join(f"• {_display_link(url)}" for url in urls)
    tail = (
        "Пришли их ещё раз — бесплатные скачивания за них возвращены."
        if refunded
        else "Пришли их ещё раз."
    )
    return f"⚠️ <b>Бот перезапускался, и эти ссылки не скачались:</b>\n{lines}\n\n{tail}"
