from __future__ import annotations

import re
from urllib.parse import urlparse

_URL_RE = re.compile(r"(?:https?://)?(?:youtu\.be|youtube(?:-nocookie)?\.com|music\.youtube\.com|m\.youtube\.com|www\.youtube\.com)[^\s<>\"']*|https?://[^\s<>\"']+")

_DOMAIN_TO_PLATFORM: dict[str, str] = {
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "m.instagram.com": "instagram",
    "ddinstagram.com": "instagram",
    "www.ddinstagram.com": "instagram",
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
    "vm.tiktok.com": "tiktok",
    "m.tiktok.com": "tiktok",
    "lite.tiktok.com": "tiktok",
    "vt.tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "m.facebook.com": "facebook",
    "fb.watch": "facebook",
    "www.fb.watch": "facebook",
    "web.facebook.com": "facebook",
    "pinterest.com": "pinterest",
    "www.pinterest.com": "pinterest",
    "pin.it": "pinterest",
    "ru.pinterest.com": "pinterest",
    "id.pinterest.com": "pinterest",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "youtu.be": "youtube",
    "music.youtube.com": "youtube",
    "youtube-nocookie.com": "youtube",
}


def parse_url(text: str) -> tuple[str, str] | None:
    match = _URL_RE.search(text)
    if not match:
        return None

    url = match.group(0)
    normalized = url if url.startswith("http") else "https://" + url
    domain = urlparse(normalized).hostname
    if not domain:
        return None

    platform = _DOMAIN_TO_PLATFORM.get(domain)
    if not platform:
        return None

    return normalized, platform
