from __future__ import annotations

import re
from urllib.parse import urlparse

# Домены, с которых мы принимаем ссылку в том числе БЕЗ схемы.
# Регексп ниже собирается из этого же списка, чтобы «поддерживаемый домен»
# был описан ровно один раз, а не дважды с риском разъехаться.
_BARE_DOMAINS = (
    "youtu.be",
    "youtube.com",
    "youtube-nocookie.com",
    "instagram.com",
    "ddinstagram.com",
    "tiktok.com",
    "facebook.com",
    "fb.watch",
    "pinterest.com",
    "pin.it",
)

_BARE_ALT = "|".join(
    [r"(?:[\w-]+\.)*" + re.escape(d) for d in _BARE_DOMAINS]
    # Мультирегиональный Pinterest отдельно: перечислять все ccTLD в
    # _BARE_DOMAINS бессмысленно — они устареют на следующем домене.
    # Описываем формой, как и в _PINTEREST_HOST_RE ниже.
    + [r"(?:[\w-]+\.)*pinterest\.[a-z]{2,4}(?:\.[a-z]{2})?"]
)

_URL_RE = re.compile(
    # Лукбихайнд обязателен: без него "notpinterest.com/pin/1" даёт совпадение
    # с позиции 3 ("pinterest.com/pin/1") и молча превращается в ссылку на
    # настоящий pinterest.com. Символ перед совпадением не должен быть частью
    # того же "слова" (буква/цифра/подчёркивание/точка/дефис).
    r"(?<![\w.-])(?:"
    # Схемная ссылка — приоритетный вариант; при равной начальной позиции
    # с бессхемной побеждает он же за счёт порядка альтернатив.
    r"https?://[^\s<>\"']+"
    # Бессхемная — только для перечисленных доменов и их поддоменов.
    rf"|(?:{_BARE_ALT})(?:/[^\s<>\"']*)?"
    r")",
    re.IGNORECASE,
)

# Хост Pinterest задаётся формой, а не перечислением: доменов вида
# pinterest.<tld> и <регион>.pinterest.com слишком много и они будут
# появляться дальше — перечень устареет на первом же новом ccTLD.
# Якорь $ обязателен — без него "pinterest.evil.com" прошёл бы проверку
# (совпадение на префиксе "pinterest." было бы найдено где угодно в строке).
# Сравнение регистронезависимо не через флаг, а через предварительный
# .lower() в _normalize_host — на вход сюда всегда приходит уже нижний регистр.
_PINTEREST_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)*pinterest\.[a-z]{2,4}(?:\.[a-z]{2})?$"
)

_DOMAIN_TO_PLATFORM: dict[str, str] = {
    "instagram.com": "instagram",
    "ddinstagram.com": "instagram",
    "tiktok.com": "tiktok",
    "vm.tiktok.com": "tiktok",
    "vt.tiktok.com": "tiktok",
    "lite.tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "web.facebook.com": "facebook",
    "fb.watch": "facebook",
    "pin.it": "pinterest",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "music.youtube.com": "youtube",
    "youtube-nocookie.com": "youtube",
}

# Поддомены-псевдонимы, которые заведомо указывают на тот же сайт: срезаем их
# ДО сравнения со словарём/регекспом, а не плодим для каждого второй ключ
# ("m.instagram.com" и "instagram.com" не должны поддерживаться порознь).
_HOST_PREFIXES = ("www.", "m.", "mobile.")

# Символы, которые в конце ссылки почти всегда принадлежат тексту, а не URL.
_TRAILING_PUNCT = ".,;:!?…'\"«»"
_BRACKET_PAIRS = {")": "(", "]": "[", "}": "{"}


def _normalize_host(host: str) -> str:
    """Нижний регистр, без завершающей точки FQDN, без www./m./mobile.-префикса."""
    host = host.lower().rstrip(".")
    changed = True
    while changed:
        changed = False
        for prefix in _HOST_PREFIXES:
            if host.startswith(prefix):
                host = host[len(prefix):]
                changed = True
                break
    return host


def _trim_trailing(url: str) -> str:
    """Срезает пунктуацию текста, случайно захваченную вместе со ссылкой.

    Закрывающая скобка обрезается, только если она непарная (открывающих
    в текущем остатке строки не меньше, чем закрывающих) — иначе ломались бы
    легитимные пути со скобками вида ".../photo/a_(b)".
    """
    while url:
        if url[-1] in _TRAILING_PUNCT:
            url = url[:-1]
            continue
        if url[-1] in _BRACKET_PAIRS:
            closing = url[-1]
            opening = _BRACKET_PAIRS[closing]
            if url.count(opening) >= url.count(closing):
                break
            url = url[:-1]
            continue
        break
    return url


def _detect_platform(host: str | None) -> str | None:
    if not host:
        return None
    normalized = _normalize_host(host)
    platform = _DOMAIN_TO_PLATFORM.get(normalized)
    if platform:
        return platform
    if _PINTEREST_HOST_RE.match(normalized):
        return "pinterest"
    return None


def parse_url(text: str) -> tuple[str, str] | None:
    match = _URL_RE.search(text)
    if not match:
        return None

    url = _trim_trailing(match.group(0))
    if not url:
        return None

    normalized = url if url.lower().startswith(("http://", "https://")) else "https://" + url
    platform = _detect_platform(urlparse(normalized).hostname)
    if not platform:
        return None

    return normalized, platform
