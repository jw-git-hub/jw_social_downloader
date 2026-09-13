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
