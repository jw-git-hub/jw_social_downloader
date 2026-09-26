"""Классификация исходов отправки файла в Telegram — чистые функции, без
обращений к aiogram-сессии.

Контекст: у собственного telegram-bot-api зашит IDLE_TIMEOUT=500с. Если
отправка молчит дольше, сервер обрывает HTTP-соединение с нашей стороны, но
файл к этому моменту может быть уже полностью доставлен получателю — ретрай
такого обрыва как обычной сетевой ошибки даёт дубль в чате. TelegramEntityTooLarge
— отдельный случай: это подкласс TelegramNetworkError, но ретраить его нет
смысла ни при каком elapsed — файл не влезает в лимит Telegram в принципе.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import Iterable

import aiohttp
from aiogram.exceptions import TelegramEntityTooLarge, TelegramNetworkError, TelegramRetryAfter

# Зашито в HttpServer.h собственного telegram-bot-api, флага на изменение нет.
SERVER_IDLE_TIMEOUT_SEC = 500
# Ниже серверного порога с запасом: после этой отметки считаем обрыв
# следствием того, что сервер уже отдал файл и захлопнул простаивающее
# соединение, а не сетевым сбоем на середине отправки.
PROBABLY_DELIVERED_AFTER_SEC = 400.0
BYTES_PER_MB = 1024 * 1024


class SendVerdict(Enum):
    RETRY = "retry"
    GIVE_UP = "give_up"
    PROBABLY_DELIVERED = "probably_delivered"
    TOO_LARGE = "too_large"


class ProbablyDeliveredError(Exception):
    """Сервер оборвал ответ после долгой отдачи, файл, скорее всего, доставлен."""


def _by_attempt(attempt: int, max_attempts: int) -> SendVerdict:
    return SendVerdict.GIVE_UP if attempt >= max_attempts - 1 else SendVerdict.RETRY


def classify_send_failure(
    exc: BaseException, elapsed_sec: float, attempt: int, max_attempts: int
) -> SendVerdict:
    """Вердикт по исключению отправки: что делать дальше.

    `attempt` считается с нуля. Порядок проверок обязателен: TelegramEntityTooLarge
    наследует TelegramNetworkError, поэтому проверяется первым — иначе он
    попал бы в общую сетевую ветку и ретраился.
    """
    if isinstance(exc, TelegramEntityTooLarge):
        return SendVerdict.TOO_LARGE
    if isinstance(exc, TelegramRetryAfter):
        return _by_attempt(attempt, max_attempts)
    if isinstance(exc, (TelegramNetworkError, asyncio.TimeoutError, aiohttp.ClientError)):
        if elapsed_sec >= PROBABLY_DELIVERED_AFTER_SEC:
            return SendVerdict.PROBABLY_DELIVERED
        return _by_attempt(attempt, max_attempts)
    return SendVerdict.GIVE_UP


def oversized_files(paths: Iterable[str], limit_mb: int) -> list[tuple[str, float]]:
    """Пары (путь, размер в МБ) для файлов строго больше лимита.

    Файл, на котором `stat()` бросает `OSError`, пропускается, а не считается
    превышением — тесты хендлера подсовывают несуществующие пути вида
    `/tmp/does-not-exist-*.mp4`, и это не должно выглядеть как оверсайз.
    """
    result: list[tuple[str, float]] = []
    for path in paths:
        try:
            size_bytes = Path(path).stat().st_size
        except OSError:
            continue
        size_mb = size_bytes / BYTES_PER_MB
        if size_mb > limit_mb:
            result.append((path, size_mb))
    return result
