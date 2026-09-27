from __future__ import annotations

import html
import math
from datetime import timedelta


def esc(value: object) -> str:
    """Экранирует значение для вставки в текст с `parse_mode=HTML`.

    `quote=False`: значения подставляются в текст сообщения, а не в атрибуты
    тегов, поэтому экранировать кавычки не нужно, а `&quot;` в русском тексте
    читается хуже самой кавычки.

    `None` даёт пустую строку, а не слово «None»: подставляем в места, где
    отсутствующее значение должно выглядеть пустым.

    ОГРАНИЧЕНИЕ: безопасна только для текстового содержимого сообщения, НЕ
    для атрибутов тегов. `quote=False` осознанно оставляет `"` неэкранированной
    — значит `esc(value)`, подставленное внутрь `<a href="{esc(value)}">`,
    не защищает от выхода за пределы атрибута (`"` в value оборвёт href и
    откроет произвольные атрибуты/теги). Ссылок с интерполируемым href в
    `bot/` пока нет, поэтому дыры сейчас нет — но если она появится,
    здесь нужна отдельная функция с `quote=True`, а не переиспользование esc().
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60


def format_wait(delta: timedelta) -> str:
    """Сколько ждать, по-человечески: «5 ч 12 мин», «3 ч», «12 мин».

    Округляем ВВЕРХ до минуты: «через 5 ч 11 мин», сказанное за 59 секунд до
    5 ч 12 мин, обмануло бы человека. Меньше минуты — «1 мин», а не «0 мин».
    """
    minutes = max(1, math.ceil(delta.total_seconds() / SECONDS_PER_MINUTE))
    hours, minutes = divmod(minutes, MINUTES_PER_HOUR)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


# Маркер строки-перечня в стиле экосистемы (см.
# .superpowers/sdd/2026-09-27-ecosystem-style/texts-plan.md, раздел 1, С5).
# "&gt; " — вид "> " после рендера HTML-разметки Telegram.
LINE_MARKER = "&gt; "


def plural_ru(count: int, forms: tuple[str, str, str]) -> str:
    """Форма русского слова по числу: `forms` = (одна, несколько, много).

    11–14 (при любой последней цифре) — «много»; иначе по последней цифре:
    1 — «одна», 2–4 — «несколько», остальное — «много».
    """
    one, few, many = forms
    if 11 <= count % 100 <= 14:
        return many
    last_digit = count % 10
    if last_digit == 1:
        return one
    if 2 <= last_digit <= 4:
        return few
    return many


STAR_FORMS = ("звезда", "звезды", "звёзд")


def stars_text(count: int) -> str:
    """«250 звёзд», «1 звезда», «2 звезды», «21 звезда»."""
    return f"{count} {plural_ru(count, STAR_FORMS)}"


PLATFORM_NAMES = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "pinterest": "Pinterest",
    "youtube": "YouTube",
}


def platform_name(platform: str) -> str:
    """Каноническое написание бренда платформы; неизвестное — `platform.capitalize()`.

    Не экранирует — экранирует вызывающий.
    """
    return PLATFORM_NAMES.get(platform, platform.capitalize())
