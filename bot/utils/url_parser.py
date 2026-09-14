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

# TLD-зоны, которыми Pinterest реально владеет как pinterest.<TLD>. Список
# ЗАКРЫТЫЙ и перечислимый намеренно — это фикс-раунд 1 после ревью:
# первая версия этого файла описывала TLD открытым классом
# `[a-z]{2,4}(?:\.[a-z]{2})?`, и это читало "evil.co" как "TLD=evil плюс
# ccTLD=co" — матчились pinterest.evil.co, pinterest.hack.io и т.п. Атакующему
# для обхода не нужен чужой бренд в домене: достаточно ЛЮБОГО домена вида
# <2-4 буквы>.<2 буквы ccTLD>, который он просто регистрирует сам, и
# поддомена "pinterest." под ним. Открытый класс путал "какой ФОРМЫ бывает
# TLD" с "кому ПРИНАДЛЕЖИТ домен" — а этот файл должен проверять именно
# принадлежность. Поддомены-регионы (ru.pinterest.com, id.pinterest.com...)
# по-прежнему заданы формой (ведущий (?:[a-z0-9-]+\.)* ниже) — это безопасно,
# потому что это поддомены ОДНОГО и того же уже проверенного апекса, а не
# отдельные домены, которыми мог бы завладеть кто-то посторонний.
_PINTEREST_TLDS = (
    "com",
    "de",
    "fr",
    "it",
    "es",
    "jp",
    "ca",
    "co.uk",
    "com.mx",
    "com.au",
)
_PINTEREST_TLD_ALT = "|".join(re.escape(tld) for tld in _PINTEREST_TLDS)

_BARE_ALT = "|".join(
    [r"(?:[\w-]+\.)*" + re.escape(d) for d in _BARE_DOMAINS]
    + [rf"(?:[\w-]+\.)*pinterest\.(?:{_PINTEREST_TLD_ALT})"]
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
    # Лукахед (?![\w.-]) — фикс-раунд 1: без него домен из _BARE_ALT матчится
    # как ПРЕФИКС более длинного чужого хоста и молча отбрасывает хвост —
    # "pinterest.evil.com/pin/1/" находил "pinterest.evil.co" (TLD-альтернативу
    # "com" читало как "co" + непойманный остаток "m"), а "youtu.be.evil.net/x"
    # находил ровно "youtu.be", отбрасывая ".evil.net/x". Тот же символьный
    # класс, что и в лукбихайнде: то, что идёт СРАЗУ после домена, не должно
    # быть буквой/цифрой/подчёркиванием/точкой/дефисом — иначе это не конец
    # хоста, а его продолжение. "?"/"#" разрешены явно — бессхемная ссылка
    # вида "youtube.com?v=abc" не должна терять query/фрагмент.
    rf"|(?:{_BARE_ALT})(?![\w.-])(?:[/?#][^\s<>\"']*)?"
    r")",
    re.IGNORECASE,
)

# Хост Pinterest — та же закрытая TLD-зона, что и в _BARE_ALT (один источник
# правды, см. _PINTEREST_TLDS выше). Якорь $ обязателен — без него
# "pinterest.evil.com" прошёл бы проверку (совпадение на префиксе
# "pinterest." было бы найдено где угодно в строке). Сравнение
# регистронезависимо не через флаг, а через предварительный .lower() в
# _normalize_host — на вход сюда всегда приходит уже нижний регистр.
_PINTEREST_HOST_RE = re.compile(
    rf"^(?:[a-z0-9-]+\.)*pinterest\.(?:{_PINTEREST_TLD_ALT})$"
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

    try:
        parsed = urlparse(normalized)
        host = parsed.hostname
        authority = parsed.netloc
    except ValueError:
        # urlparse может бросить ValueError на хостах, чьи символы меняются
        # под NFKC-нормализацией (гомоглиф-трюки вида "pinterest℀.com") —
        # глобального обработчика ошибок в проекте нет, поэтому непойманное
        # исключение здесь означает, что пользователь вообще не получит
        # ответа на любое сообщение с таким паттерном. Неподдерживаемая
        # ссылка — это None, а не краш.
        return None

    # Бэкслэш в authority — сигнал parser-differential атаки: Python здесь
    # видит "userinfo@host" ровно так, как написано, и корректно вычисляет
    # host, а часть библиотек (например urllib3, которым пользуется
    # gallery-dl) трактует "\" как эквивалент "/" и обрывает authority
    # раньше — "https://127.0.0.1\@pinterest.com/pin/1/" для нас выглядит
    # как pinterest.com, а для другого парсера это запрос на 127.0.0.1.
    # Легитимных ссылок с бэкслэшем в authority ни у одной из платформ нет.
    if "\\" in authority:
        return None

    platform = _detect_platform(host)
    if not platform:
        return None

    return normalized, platform
