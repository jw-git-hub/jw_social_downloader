from __future__ import annotations

import html


def esc(value: object) -> str:
    """Экранирует значение для вставки в текст с `parse_mode=HTML`.

    `quote=False`: значения подставляются в текст сообщения, а не в атрибуты
    тегов, поэтому экранировать кавычки не нужно, а `&quot;` в русском тексте
    читается хуже самой кавычки.

    `None` даёт пустую строку, а не слово «None»: подставляем в места, где
    отсутствующее значение должно выглядеть пустым.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=False)
