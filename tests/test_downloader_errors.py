"""Классификация ошибок загрузчика.

Ключевая проверка файла — негативная: раньше любая ошибка yt-dlp
классифицировалась как «cookies устарели», потому что yt-dlp дописывает
совет «Use --cookies ...» почти в каждое своё сообщение об ошибке.
"""

from bot.services.downloader import _error_surface, _parse_error

# ── Реальные фрагменты stderr ────────────────────────────────────────────

YTDLP_BOT_CHECK = (
    "[youtube] Extracting URL: https://www.youtube.com/watch?v=aaaaaaaaaaa\n"
    "[youtube] aaaaaaaaaaa: Downloading webpage\n"
    "ERROR: [youtube] aaaaaaaaaaa: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication. "
    "See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ  for how to manually pass cookies.\n"
)

YTDLP_GENERIC_WITH_COOKIE_HINT = (
    "[instagram] Setting up cookies from /tmp/jw_downloads/jw_cookies_x/cookies.txt\n"
    "ERROR: [instagram] DBc1: Unable to extract shared data; "
    "please report this issue. Use --cookies for the authentication.\n"
)

YTDLP_JSON_TRACEBACK = (
    "ERROR: [instagram] DBc1: Failed to parse JSON "
    "(caused by JSONDecodeError('Expecting value in \\'\\' line 1 column 1'))\n"
)

GALLERY_DL_LOGIN_REDIRECT = "[instagram][error] HTTP redirect to login page\n"

YTDLP_PRIVATE_KEY_NOISE = (
    "WARNING: unable to load private key from /etc/ssl/private key store\n"
    "ERROR: [facebook] 123: Unable to download webpage: HTTP Error 500\n"
)

YTDLP_SEPARATE_NOISE = (
    "ERROR: [tiktok] 7: Requested format is not available; "
    "video and audio are stored in separate streams\n"
)

YTDLP_NOT_FOUND = "ERROR: [tiktok] 7: Unable to download webpage: HTTP Error 404: Not Found\n"

YTDLP_PRIVATE = "ERROR: [instagram] DBc1: This post is private and cannot be downloaded\n"

# ── Фикс-раунд 1: фрагменты из живой ревью ───────────────────────────────

YTDLP_FILE_TOO_LARGE = (
    "[download] File is larger than max-filesize (1500.00MiB > 1500MiB). Aborting.\n"
)

YTDLP_GEO_BLOCKED_PHRASED = (
    "ERROR: [youtube] aaa: The uploader has not made this video available in your country\n"
)

YTDLP_GEO_VS_UNAVAILABLE = (
    "ERROR: [tiktok] 7: Video unavailable. This content is blocked in your country\n"
)

INSTAGRAM_DEAD_SESSION_VS_RATE = (
    "ERROR: [instagram] DBc1: rate-limit reached or login required\n"
)

GALLERY_DL_WARNING_THEN_ERROR = (
    "[pinterest][warning] NameResolutionError: Failed to resolve 'api.pinterest.com'\n"
    "[pinterest][error] API request failed\n"
)

YTDLP_SECRET_LADEN_UNCLASSIFIED = (
    "ERROR: [instagram] weird failure fetching "
    "https://scontent.cdninstagram.com/v/image.jpg?_nc_ht=x&oe=1&sig=SUPERSECRETSIGNATURE123\n"
)


# ── _error_surface ───────────────────────────────────────────────────────


def test_error_surface_keeps_only_error_lines():
    surface = _error_surface(YTDLP_BOT_CHECK)
    assert "Extracting URL" not in surface
    assert "Downloading webpage" not in surface
    assert "Sign in to confirm" in surface


def test_error_surface_understands_gallery_dl_marker():
    assert "HTTP redirect to login page" in _error_surface(GALLERY_DL_LOGIN_REDIRECT)


def test_error_surface_is_empty_when_nothing_looks_like_an_error():
    assert _error_surface("[youtube] aaa: Downloading webpage\n") == ""


# ── Негативные проверки: то, ради чего задача ────────────────────────────


def test_cookie_hint_in_ytdlp_error_is_not_reported_as_expired_cookies():
    msg = _parse_error([("yt-dlp", YTDLP_BOT_CHECK)], "youtube")
    assert "🍪" not in msg
    assert "🤖" in msg


def test_cookie_flag_echo_is_not_reported_as_expired_cookies():
    msg = _parse_error([("yt-dlp", YTDLP_GENERIC_WITH_COOKIE_HINT)], "instagram")
    assert "🍪" not in msg


def test_private_key_noise_is_not_reported_as_private_video():
    msg = _parse_error([("yt-dlp", YTDLP_PRIVATE_KEY_NOISE)], "facebook")
    assert "🔒" not in msg


def test_word_separate_is_not_reported_as_rate_limit():
    msg = _parse_error([("yt-dlp", YTDLP_SEPARATE_NOISE)], "tiktok")
    assert "⏳" not in msg
    assert "🔄" in msg


# ── Позитивные проверки ──────────────────────────────────────────────────


def test_gallery_dl_login_redirect_wins_over_ytdlp_json_traceback():
    msg = _parse_error(
        [("yt-dlp", YTDLP_JSON_TRACEBACK), ("gallery-dl", GALLERY_DL_LOGIN_REDIRECT)],
        "instagram",
    )
    assert "🍪" in msg


def test_http_404_is_reported_as_not_found():
    assert "🔍" in _parse_error([("yt-dlp", YTDLP_NOT_FOUND)], "tiktok")


def test_private_post_is_reported_as_private():
    assert "🔒" in _parse_error([("yt-dlp", YTDLP_PRIVATE)], "instagram")


def test_unclassified_error_shows_gallery_dl_text_escaped():
    msg = _parse_error(
        [("yt-dlp", YTDLP_JSON_TRACEBACK), ("gallery-dl", "[pinterest][error] weird <thing> & co\n")],
        "pinterest",
    )
    assert "&lt;thing&gt;" in msg
    assert "&amp;" in msg
    assert "<code>" in msg


def test_unclassified_error_falls_back_to_ytdlp_when_gallery_dl_silent():
    msg = _parse_error([("yt-dlp", YTDLP_JSON_TRACEBACK), ("gallery-dl", "")], "instagram")
    assert "Failed to parse JSON" in msg


def test_empty_outputs_never_produce_an_empty_message():
    msg = _parse_error([], "youtube")
    assert msg.strip()
    assert "<code>" in msg
    # Холостой тест ревью фикс-раунда 1: `<code></code>` ПУСТЫМ тоже прошёл
    # бы оба ассерта выше — видимый текст вокруг code-блока ненулевой
    # независимо от содержимого внутри. Проверяем содержимое явно.
    assert "<code></code>" not in msg
    assert "утилита завершилась без сообщения об ошибке" in msg


# ── Фикс-раунд 1: маскирование секретов (Important #3) ───────────────────


def test_unclassified_error_masks_query_string_secrets():
    msg = _parse_error([("yt-dlp", YTDLP_SECRET_LADEN_UNCLASSIFIED)], "instagram")
    assert "SUPERSECRETSIGNATURE123" not in msg
    # mask_secrets работает ДО html.escape (иначе `&` → `&amp;` сломал бы её
    # же паттерн `[?&]sig=`) — поэтому в итоговом, уже экранированном тексте
    # маркер выглядит как `&lt;redacted&gt;`, а не как `<redacted>`.
    assert "&lt;redacted&gt;" in msg


# ── Фикс-раунд 1: too_large недостижим без ERROR: (Minor #6) ─────────────


def test_file_too_large_marker_without_error_prefix_is_recognized():
    assert "📦" in _parse_error([("yt-dlp", YTDLP_FILE_TOO_LARGE)], "youtube")


# ── Фикс-раунд 1: гео — реальная фраза и порядок правил (Minor #7) ───────


def test_geo_blocked_real_phrasing_is_recognized():
    msg = _parse_error([("yt-dlp", YTDLP_GEO_BLOCKED_PHRASED)], "youtube")
    assert "🌍" in msg


def test_geo_block_wins_over_generic_unavailable_when_both_present():
    msg = _parse_error([("yt-dlp", YTDLP_GEO_VS_UNAVAILABLE)], "tiktok")
    assert "🌍" in msg
    assert "🔍" not in msg


# ── Фикс-раунд 1: инвариант порядка правил (холостой тест #2) ────────────


def test_dead_session_phrase_wins_over_generic_rate_limit_pattern():
    """«rate-limit reached» — фирменная фраза Instagram про МЁРТВУЮ СЕССИЮ
    (см. комментарий у dead_session), а не троттлинг. Она же попадает и под
    общий rate_limit-паттерн (`\\brate[- ]limit`) — порядок правил решает,
    какой смысл выиграет. Раньше не было теста, закрепляющего порядок:
    поднять rate_limit выше dead_session в `_ERROR_RULES` — весь сьют
    оставался зелёным."""
    msg = _parse_error([("yt-dlp", INSTAGRAM_DEAD_SESSION_VS_RATE)], "instagram")
    assert "🍪" in msg
    assert "⏳" not in msg


# ── Фикс-раунд 1: показ сохраняет [warning] с настоящей причиной (Minor #8) ─


def test_unclassified_message_keeps_warning_line_with_the_real_cause():
    msg = _parse_error([("gallery-dl", GALLERY_DL_WARNING_THEN_ERROR)], "pinterest")
    assert "NameResolutionError" in msg
    assert "API request failed" in msg
