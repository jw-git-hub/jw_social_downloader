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
