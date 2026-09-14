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
    # "PL.PINTEREST.COM" не является ключом ни одного словаря — платформа
    # определяется только через регексп по форме хоста, и именно поэтому
    # этот тест чувствителен к тому, что хост приводится к нижнему регистру
    # ДО сравнения с регекспом (сам регексп не компилируется с re.IGNORECASE).
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
