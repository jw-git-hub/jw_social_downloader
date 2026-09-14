"""Тесты на bot.utils.url_parser.

До этого файла модуль не был покрыт тестами вообще, хотя именно он решает,
примет бот присланную ссылку или ответит «Это не похоже на ссылку». Аллоулист
хостов внутри — единственная защита от SSRF: контейнер работает в
network_mode: host, поэтому "127.0.0.1" изнутри него — это хостовой loopback,
а не изолированный сетевой namespace. Негативные проверки здесь так же важны,
как позитивные, и их нельзя вычищать под предлогом «мешает».
"""

import pytest

from bot.utils.url_parser import parse_url


# ── Позитив: по одной ссылке на каждую поддерживаемую платформу ──────────


@pytest.mark.parametrize(
    "text, platform",
    [
        ("https://www.instagram.com/p/DBc1abc/", "instagram"),
        ("https://www.instagram.com/reel/DBc1abc/", "instagram"),
        ("https://www.tiktok.com/@user/video/7123456789012345678", "tiktok"),
        ("https://vm.tiktok.com/ZSabc123/", "tiktok"),
        ("https://www.facebook.com/watch/?v=123456789", "facebook"),
        ("https://fb.watch/abcDEF/", "facebook"),
        ("https://www.pinterest.com/pin/123456789012345678/", "pinterest"),
        ("https://www.youtube.com/watch?v=aaaaaaaaaaa", "youtube"),
        ("https://youtu.be/aaaaaaaaaaa", "youtube"),
    ],
)
def test_supported_links_are_recognised(text, platform):
    result = parse_url(text)
    assert result is not None, text
    assert result[1] == platform


# ── Второстепенные домены той же платформы ────────────────────────────────
# Это тоже "уже работает", но раньше не было закрыто ни одним тестом.
# Часть хостов ниже совпадала с ключами старого словаря дословно, поэтому
# эти конкретные случаи не отличают старый код от нового — они фиксируют
# уже работающее поведение файла, который не был закрыт ничем.


@pytest.mark.parametrize(
    "text, platform",
    [
        ("https://m.instagram.com/p/DBc1abc/", "instagram"),
        ("https://www.ddinstagram.com/p/DBc1abc/", "instagram"),
        ("https://m.tiktok.com/@user/video/123456/", "tiktok"),
        ("https://lite.tiktok.com/@user/video/123456/", "tiktok"),
        ("https://vt.tiktok.com/ZSabc/", "tiktok"),
        ("https://m.facebook.com/watch/?v=123", "facebook"),
        ("https://web.facebook.com/watch/?v=123", "facebook"),
        ("https://www.fb.watch/abcDEF/", "facebook"),
        ("https://music.youtube.com/watch?v=aaaaaaaaaaa", "youtube"),
        ("https://youtube-nocookie.com/embed/aaaaaaaaaaa", "youtube"),
        ("https://m.youtube.com/watch?v=aaaaaaaaaaa", "youtube"),
    ],
)
def test_secondary_platform_domains_are_recognised(text, platform):
    result = parse_url(text)
    assert result is not None, text
    assert result[1] == platform


# ── Региональные домены Pinterest ─────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "pinterest.com", "www.pinterest.com", "m.pinterest.com",
        "ru.pinterest.com", "id.pinterest.com", "br.pinterest.com",
        "in.pinterest.com", "tr.pinterest.com", "nl.pinterest.com",
        "pl.pinterest.com", "pinterest.de", "pinterest.fr", "pinterest.it",
        "pinterest.es", "pinterest.jp", "pinterest.ca", "pinterest.co.uk",
        "pinterest.com.mx", "pinterest.com.au",
    ],
)
def test_regional_pinterest_hosts_are_accepted(host):
    result = parse_url(f"https://{host}/pin/123456789012345678/")
    assert result is not None, host
    assert result[1] == "pinterest"


def test_host_case_is_normalised_for_regional_pinterest():
    # Поправка после ревью: комментарий здесь раньше приписывал
    # регистронезависимость вызову .lower() внутри _normalize_host — это
    # холостое утверждение, мутация подтвердила: urlparse(...).hostname САМ
    # всегда возвращает нижний регистр (штатное поведение stdlib), поэтому
    # ко входу _normalize_host он приходит уже нормализованным, и .lower()
    # там избыточен для любого вызова через публичный parse_url. Тест
    # остаётся — он честно проверяет ИТОГОВОЕ поведение parse_url на хосте,
    # которого нет ни в одном словаре (только через регексп по форме), а не
    # то, какая именно строчка кода регистр приводит.
    result = parse_url("HTTPS://PL.PINTEREST.COM/pin/123456789012345678/")
    assert result is not None
    assert result[1] == "pinterest"


def test_trailing_dot_in_hostname_is_stripped():
    # FQDN с завершающей точкой — валидный хост ("pinterest.de." == "pinterest.de"),
    # но точка ломает и словарное, и регекспное сравнение, если её не срезать.
    result = parse_url("https://pinterest.de./pin/123456789012345678/")
    assert result is not None
    assert result[1] == "pinterest"


# ── Ссылки без схемы ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, platform",
    [
        ("pin.it/abcDEF12", "pinterest"),
        ("www.pinterest.com/pin/123/", "pinterest"),
        ("pinterest.co.uk/pin/123/", "pinterest"),
        ("instagram.com/p/DBc1abc/", "instagram"),
        ("vt.tiktok.com/ZSabc/", "tiktok"),
        ("m.tiktok.com/@user/video/123456/", "tiktok"),
        ("youtu.be/aaaaaaaaaaa", "youtube"),
    ],
)
def test_schemeless_links_are_accepted_and_normalised(text, platform):
    result = parse_url(text)
    assert result is not None, text
    url, detected = result
    assert detected == platform
    assert url.startswith("https://")


def test_schemeless_link_keeps_query_string():
    # Regression (ревью, фикс-раунд 1): до анкера-лукахеда в бессхемной ветке
    # опциональная группа пути матчила только "/...", поэтому "?v=abc" без
    # ведущего "/" отбрасывался целиком — "youtube.com?v=abc" превращалось в
    # ссылку на главную страницу, а не на конкретное видео.
    result = parse_url("youtube.com?v=aaaaaaaaaaa")
    assert result is not None
    assert result == ("https://youtube.com?v=aaaaaaaaaaa", "youtube")


# ── Query-строка сохраняется целиком ──────────────────────────────────────


def test_query_string_is_preserved():
    text = "https://www.youtube.com/watch?v=aaaaaaaaaaa&t=42s"
    result = parse_url(text)
    assert result is not None
    assert result[0] == text


# ── Хвостовая пунктуация ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Смотри https://www.instagram.com/p/DBc1abc/.", "https://www.instagram.com/p/DBc1abc/"),
        ("https://pin.it/abcDEF12,", "https://pin.it/abcDEF12"),
        ("(https://youtu.be/aaaaaaaaaaa)", "https://youtu.be/aaaaaaaaaaa"),
        ("https://www.tiktok.com/@u/video/7123!", "https://www.tiktok.com/@u/video/7123"),
        ("Вот: https://fb.watch/abcDEF/…", "https://fb.watch/abcDEF/"),
        ("Ссылка? https://www.pinterest.com/pin/123/;", "https://www.pinterest.com/pin/123/"),
        # Два хвостовых символа подряд — точка и непарная скобка. Проверяет,
        # что обрезка работает циклом, а не одним снятием символа.
        ("(https://youtu.be/aaaaaaaaaaa).", "https://youtu.be/aaaaaaaaaaa"),
    ],
)
def test_trailing_punctuation_is_trimmed(text, expected):
    result = parse_url(text)
    assert result is not None, text
    assert result[0] == expected


def test_balanced_closing_bracket_is_kept():
    # Скобка парная — значит она часть адреса, а не обрамление из текста.
    url = "https://www.facebook.com/photo/a_(b)"
    result = parse_url(url)
    assert result is not None
    assert result[0] == url


# ── Негатив: аллоулист — единственная защита от SSRF ──────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "http://127.0.0.1/admin",
        "http://127.0.0.1:9999/internal",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "file:///etc/passwd",
        "https://evil.example/?redirect=https://www.instagram.com/p/DBc1/",
        "https://pinterest.com.evil.net/pin/1/",
        "https://notpinterest.com/pin/1/",
        # Бессхемный вариант той же подделки: без лукбихайнда регексп нашёл бы
        # «pinterest.com/pin/1/» внутри чужого домена и подставил схему.
        "notpinterest.com/pin/1/",
        "my-instagram.com/p/DBc1/",
        "https://instagram.com.evil.net/p/1/",
        # Числовой (десятичный) IPv4-адрес loopback — классический обход
        # блок-листов. Аллоулисту он не страшен: строка "2130706433" не
        # совпадает ни с одним известным доменом, что бы с ней ни делал
        # нижестоящий HTTP-клиент.
        "http://2130706433/admin",
        # IPv6-адрес, отображённый на loopback.
        "http://[::ffff:127.0.0.1]/",
        # Пустой authority — до урезания схемы дело не доходит, но
        # urlparse(...).hostname должен корректно дать None, а не упасть.
        "https:///path",
        "просто текст без ссылки",
        "",
    ],
)
def test_hostile_and_unsupported_urls_are_rejected(text):
    assert parse_url(text) is None, text


def test_userinfo_trick_does_not_bypass_the_allowlist():
    # Хост здесь — 127.0.0.1, а не pinterest.com: всё, что до "@", это userinfo.
    assert parse_url("https://www.pinterest.com@127.0.0.1/pin/1/") is None


def test_schemed_url_wins_over_bare_domain_later_in_the_text():
    result = parse_url("https://youtu.be/aaaaaaaaaaa и ещё pin.it/abc")
    assert result is not None
    assert result[1] == "youtube"


# ── Фикс-раунд 1 (ревью): два Critical SSRF и два Important ──────────────
#
# C-1: TLD Pinterest раньше матчился открытым классом [a-z]{2,4}(?:\.[a-z]{2})?
# — "evil.co" читался как "TLD=evil плюс ccTLD=co", и pinterest.evil.co/
# pinterest.hack.io проходили аллоулист. Атакующему не нужен чужой бренд в
# домене — только короткий домен под коротким ccTLD, который он регистрирует
# сам. Закрыто явным перечнем TLD, которыми Pinterest реально владеет
# (_PINTEREST_TLDS в url_parser.py).
#
# C-2: бессхемная ветка матчила домен из _BARE_ALT как ПРЕФИКС более
# длинного чужого хоста и молча отбрасывала хвост — "pinterest.evil.com/pin/1/"
# находил "pinterest.evil.co" (TLD-альтернативу "com" читало как "co" плюс
# непойманный остаток "m"), "youtu.be.evil.net/x" находил ровно "youtu.be".
# Ветка не "проходила тот же фильтр", что схемная, — она ПОДМЕНЯЛА хост ДО
# фильтра. Закрыто лукахедом (?![\w.-]) сразу после домена.


@pytest.mark.parametrize(
    "text",
    [
        "https://pinterest.evil.co/pin/1/",
        "https://pinterest.evil.co:6379/",
        "https://pinterest.hack.io/x",
        "https://sub.pinterest.evil.co/x",
        "https://pinterest.evil.me/x",
        "https://pinterest.evil.ly/x",
    ],
)
def test_pinterest_tld_squatting_is_rejected(text):
    # C-1. До фикса открытый класс [a-z]{2,4}(?:\.[a-z]{2})? принимал ЛЮБОЙ
    # 2-4-буквенный домен под ЛЮБЫМ двухбуквенным ccTLD как "TLD Pinterest".
    assert parse_url(text) is None, text


@pytest.mark.parametrize(
    "text",
    [
        # Бессхемные близнецы уже существующих схемных негативов из секции
        # выше ("pinterest.com.evil.net", "instagram.com.evil.net" и т.п.)
        # — именно в бессхемной ветке была дыра C-2, схемная всегда матчила
        # весь хвост целиком через [^\s<>"']+ и уже была безопасна.
        "pinterest.evil.com/pin/1/",
        "instagram.com.evil.net/p/1/",
        "youtu.be.evil.net/x",
        "tiktok.com.evil.net/@u/video/1",
        "facebook.com.evil.net/x",
        "fb.watch.evil.net/x",
        "pin.it.evil.net/x",
    ],
)
def test_bare_domain_match_does_not_truncate_to_a_shorter_prefix(text):
    # C-2. До фикса бессхемная ветка матчила ровно домен-литерал/TLD-форму и
    # молча останавливалась там же, где чужой хост лишь НАЧИНАЛСЯ с похожей
    # на настоящую подстроки — а не отвергала ссылку с неопознанным хвостом.
    assert parse_url(text) is None, text


def test_nfkc_homoglyph_host_does_not_raise():
    # I-1. urlparse бросает ValueError на хостах, чьи символы меняются под
    # NFKC-нормализацией ("℀" нормализуется в "a/c") — глобального
    # обработчика ошибок в проекте нет, поэтому непойманное исключение
    # раньше означало, что пользователь не получал ответа вообще на любое
    # сообщение с таким паттерном. Неподдерживаемая ссылка — это None.
    assert parse_url("https://pinterest℀.com/x") is None


def test_backslash_in_authority_is_rejected():
    # I-2. Python здесь видит валидный "userinfo@host" и корректно относит
    # всё до "@" к userinfo, отдавая host=pinterest.com — а urllib3 (её
    # использует gallery-dl) трактует "\" как эквивалент "/" и обрывает
    # authority на "127.0.0.1" раньше "@". Один и тот же текст ссылки
    # означает разный хост для разных библиотек внутри одного и того же
    # бота — parser-differential SSRF. Легитимных ссылок с "\" в authority
    # ни у одной из пяти платформ нет.
    assert parse_url(r"https://127.0.0.1\@pinterest.com/pin/1/") is None


def test_backslash_outside_authority_does_not_trigger_rejection():
    # Проверка границы фикса I-2: запрет должен быть специфичен именно для
    # authority, а не для URL целиком — обратный слэш в пути (сам по себе
    # синтаксически нестандартный, но не создающий host-confusion) не повод
    # отвергать ссылку.
    result = parse_url("https://www.instagram.com/p/a\\b/")
    assert result is not None
    assert result[1] == "instagram"
