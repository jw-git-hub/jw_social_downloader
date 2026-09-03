# Локальный Bot API, прогресс и уборка — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести бота с облачного Bot API (потолок 50 МБ) на собственный `telegram-bot-api` (1500 МБ), показывать пользователю прогресс загрузки и отдачи, и гарантировать удаление временных файлов на всех путях выполнения.

**Architecture:** Отдельный контейнер `telegram-bot-api` в режиме `--local`; бот и сервер видят одну и ту же папку загрузок по одинаковому пути, поэтому файл отдаётся ссылкой `file:///…`, а не multipart-загрузкой. Прогресс загрузки берётся из потокового stdout yt-dlp; прогресс отдачи оценивается по скользящей средней фактической скорости. Уборка делается структурной: рабочую папку создаёт хендлер и удаляет её в `finally`, накрывающем и загрузку, и отправку.

**Tech Stack:** Python 3.12, aiogram 3.30.0, yt-dlp, gallery-dl, ffprobe (ffmpeg 7.1), Docker Compose, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-03-local-bot-api-design.md` — читать вместе с планом.

## Global Constraints

- Python 3.12 внутри контейнера; хостовой Python 3.10 сломан (битый aiogram) — **тесты запускать только в контейнере**.
- aiogram зафиксирован как `>=3.4,<4.0`, фактически 3.30.0. `SendVideo.video: str | InputFile`.
- `TelegramEntityTooLarge` **наследует** `TelegramNetworkError` — порядок `except` имеет значение.
- У сервера `telegram-bot-api` жёсткий `IDLE_TIMEOUT = 500` секунд без флага. Отправка, упершаяся в него, **всё равно доставляет файл** — ретраить нельзя.
- Стат-порт сервера (`8082`) отдаёт токен бота открытым текстом — **не публиковать и не использовать**.
- Папка загрузок обязана лежать **вне** рабочего каталога сервера, иначе сборщик мусора TDLib удалит наши файлы.
- Всё крупное — только на `/mnt/storage` (1.3 ТБ). На eMMC (`/`) остаётся 2.3 ГБ — туда ничего не писать.
- `network_mode: host` у бота **сохранять** — Docker-NAT на этом хосте роняет аплоады крупнее ~1.5 МБ.
- Приоритет H.264/avc1 над AV1 для Facebook **сохранять** — иначе «звук без картинки».
- Эфемерные копии cookies **сохранять** — yt-dlp переписывает файл кук и убивает `sessionid`.
- Репозиторий публичный: секретов в код и в `.env.example` не класть, только имена переменных и плейсхолдеры.
- Стартовое `MAX_FILE_SIZE_MB = 1500`. Поднимать до 1900 только по результату замера в Задаче 13.
- Все пользовательские тексты — на русском, в стиле существующих сообщений бота.

---

### Task 1: Тестовая инфраструктура

Тестов в проекте нет вообще, pytest не установлен ни на хосте, ни в образе. Без этой задачи остальные не смогут следовать TDD.

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`
- Create: `scripts/test.sh`
- Modify: `Dockerfile` (сделать многостадийным)
- Modify: `docker-compose.yml` (у сервиса `bot` указать `target: base`)

**Interfaces:**
- Consumes: ничего.
- Produces: команда `./scripts/test.sh` собирает стадию `test` и прогоняет pytest. Все последующие задачи используют её как единственный способ запуска тестов.

- [ ] **Step 1: Создать `requirements-dev.txt`**

```
pytest>=8.0
pytest-asyncio>=0.24
```

- [ ] **Step 2: Сделать `Dockerfile` многостадийным**

Целиком заменить содержимое на:

```dockerfile
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]" gallery-dl

COPY . .

RUN mkdir -p /app/data /srv/jw_downloads

CMD ["python", "-m", "bot"]

# Стадия для прогона тестов. В прод-образ (target: base) не попадает.
FROM base AS test
RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["python", "-m", "pytest", "-q"]
```

- [ ] **Step 3: Закрепить прод-стадию в `docker-compose.yml`**

У сервиса `bot` заменить строку `build: .` на:

```yaml
    build:
      context: .
      target: base
```

- [ ] **Step 4: Создать `scripts/test.sh`**

```bash
#!/usr/bin/env bash
# Единственный поддерживаемый способ прогона тестов: внутри образа,
# потому что хостовой Python 3.10 в этом окружении имеет битый aiogram.
set -euo pipefail
cd "$(dirname "$0")/.."
docker build --target test -t jw_downloader:test .
docker run --rm jw_downloader:test python -m pytest "$@"
```

Сделать исполняемым: `chmod +x scripts/test.sh`

- [ ] **Step 5: Создать `tests/__init__.py`** — пустой файл.

- [ ] **Step 6: Создать `tests/conftest.py`**

`bot/config.py` создаёт `Settings()` на импорте и требует `BOT_TOKEN` и `ADMIN_ID`. В образе нет `.env` (он в `.dockerignore`), поэтому переменные надо задать до импорта любого модуля бота. `conftest.py` импортируется раньше тестовых модулей, так что установка на верхнем уровне работает.

```python
import os

# Должно стоять ДО любого импорта из bot.* — bot/config.py инстанцирует
# Settings() на импорте и падает без этих переменных.
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("DOWNLOAD_ROOT", "/tmp/jw_test_downloads")

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 7: Создать `tests/test_smoke.py`**

```python
def test_bot_config_imports():
    from bot.config import settings

    assert settings.BOT_TOKEN == "123456:TEST-TOKEN-NOT-REAL"
```

- [ ] **Step 8: Создать `pytest.ini`**

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

- [ ] **Step 9: Прогнать тесты**

Run: `./scripts/test.sh -v`
Expected: PASS, 1 passed.

- [ ] **Step 10: Commit**

```bash
git add requirements-dev.txt Dockerfile docker-compose.yml pytest.ini scripts/test.sh tests/
git commit -m "test: add pytest infrastructure with a dedicated Docker stage"
```

---

### Task 2: Новые настройки конфигурации

**Files:**
- Modify: `bot/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `settings.TELEGRAM_API_BASE: str`, `settings.TELEGRAM_REQUEST_TIMEOUT: int`, `settings.DOWNLOAD_ROOT: str`, `settings.MIN_FREE_DISK_GB: int`, `settings.CLEANUP_MAX_AGE_MIN: int`, изменённые `settings.MAX_FILE_SIZE_MB` и `settings.DOWNLOAD_TIMEOUT`. Используются в задачах 4–12.

- [ ] **Step 1: Написать падающий тест `tests/test_config.py`**

```python
def test_new_transport_settings_have_expected_defaults():
    from bot.config import settings

    assert settings.TELEGRAM_API_BASE == "http://127.0.0.1:8081"
    assert settings.TELEGRAM_REQUEST_TIMEOUT == 900
    assert settings.MIN_FREE_DISK_GB == 5
    assert settings.CLEANUP_MAX_AGE_MIN == 45


def test_size_and_timeout_raised_for_local_api():
    from bot.config import settings

    # Стартовое значение; поднимается до 1900 только после замера (Задача 13).
    assert settings.MAX_FILE_SIZE_MB == 1500
    # 2 ГБ на ~10 МБ/с не укладываются в прежние 120 секунд.
    assert settings.DOWNLOAD_TIMEOUT == 900


def test_request_timeout_exceeds_server_idle_timeout():
    from bot.config import settings

    # У telegram-bot-api жёсткий IDLE_TIMEOUT=500 с. Наш клиентский таймаут
    # должен быть заведомо больше, чтобы мы наблюдали закрытие сервером,
    # а не собственный таймаут.
    assert settings.TELEGRAM_REQUEST_TIMEOUT > 500
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'TELEGRAM_API_BASE'`.

- [ ] **Step 3: Добавить настройки в `bot/config.py`**

Внутри класса `Settings`, после `TIKTOK_PROXY`, добавить:

```python
    # ── Локальный Bot API ──
    # Адрес самостоятельно поднятого telegram-bot-api (см. docker-compose.yml).
    TELEGRAM_API_BASE: str = "http://127.0.0.1:8081"
    # Таймаут HTTP-запроса к нему. Заведомо больше серверного IDLE_TIMEOUT=500,
    # чтобы соединение закрывал сервер, а не мы — так поведение предсказуемо.
    TELEGRAM_REQUEST_TIMEOUT: int = 900

    # ── Файлы ──
    # Папка загрузок. Общая с контейнером telegram-bot-api по ОДИНАКОВОМУ пути:
    # бот отдаёт файлы ссылкой file://, и сервер должен разрешить тот же путь.
    DOWNLOAD_ROOT: str = "/srv/jw_downloads"
    # Ниже этого порога свободного места загрузку не начинаем.
    MIN_FREE_DISK_GB: int = 5
    # Возраст, после которого подметальщик считает папку осиротевшей.
    CLEANUP_MAX_AGE_MIN: int = 45
```

И изменить два существующих поля:

```python
    MAX_FILE_SIZE_MB: int = 1500
    DOWNLOAD_TIMEOUT: int = 900
```

- [ ] **Step 4: Прогнать тест**

Run: `./scripts/test.sh tests/test_config.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Обновить `.env.example`**

Заменить блок «Лимиты / цены» и добавить блок транспорта:

```
# ── Локальный Bot API сервер ──
# Ключи приложения с https://my.telegram.org (вкладка API development tools).
# Это ключи ПРИЛОЖЕНИЯ, не бота. Обязательны для контейнера telegram-bot-api.
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
# Адрес локального сервера (совпадает с портом из docker-compose.yml)
# TELEGRAM_API_BASE=http://127.0.0.1:8081
# TELEGRAM_REQUEST_TIMEOUT=900

# ── Файлы ──
# Папка загрузок внутри контейнера. Смонтирована с /mnt/storage и должна
# совпадать с путём, смонтированным в контейнер telegram-bot-api.
# DOWNLOAD_ROOT=/srv/jw_downloads
# MIN_FREE_DISK_GB=5
# CLEANUP_MAX_AGE_MIN=45

# ── Лимиты / цены (опционально, есть значения по умолчанию) ──
# FREE_DOWNLOADS=3
# Потолок выведен из бюджета IDLE_TIMEOUT=500 с у telegram-bot-api,
# а не из лимита API в 2000 МБ. Поднимать только после замера реальной скорости.
# MAX_FILE_SIZE_MB=1500
# DOWNLOAD_TIMEOUT=900
# SUBSCRIPTION_PRICE_USDT=5
# SUBSCRIPTION_PRICE_VND=125000
# SUBSCRIPTION_PRICE_THB=175
```

Также дописать отсутствующий `USDT_TRC20_ADDRESS`, если он ещё не заполнен плейсхолдером.

- [ ] **Step 6: Commit**

```bash
git add bot/config.py .env.example tests/test_config.py
git commit -m "feat(config): add local Bot API settings, raise size and timeout limits"
```

---

### Task 3: ffprobe-гейт целостности медиа

Ловит video-only DASH-фрагмент и обрезанные файлы до того, как будет потрачено несколько минут на отдачу. Даёт реальные `width`/`height` для `reply_video`.

**Files:**
- Create: `bot/services/media_probe.py`
- Test: `tests/test_media_probe.py`

**Interfaces:**
- Produces:
  - `MediaInfo` — frozen dataclass с полями `has_video: bool`, `has_audio: bool`, `duration: float`, `width: int`, `height: int`.
  - `parse_ffprobe_json(raw: str) -> MediaInfo | None`
  - `async probe_media(path: Path) -> MediaInfo | None`
  - `video_reject_reason(info: MediaInfo | None) -> str | None` — `None`, если файл годен к отправке как видео.
- Потребители: Задача 10 (перед отправкой) и Задача 11 (`width`/`height` в `reply_video`).

- [ ] **Step 1: Написать падающий тест `tests/test_media_probe.py`**

```python
import json

import pytest

from bot.services.media_probe import (
    MediaInfo,
    parse_ffprobe_json,
    video_reject_reason,
)


def _ffprobe_output(streams, duration="12.5"):
    return json.dumps({"streams": streams, "format": {"duration": duration}})


def test_parses_normal_video_with_audio():
    raw = _ffprobe_output([
        {"codec_type": "video", "width": 1920, "height": 1080},
        {"codec_type": "audio"},
    ])
    info = parse_ffprobe_json(raw)
    assert info == MediaInfo(
        has_video=True, has_audio=True, duration=12.5, width=1920, height=1080
    )


def test_detects_video_only_dash_fragment():
    raw = _ffprobe_output([{"codec_type": "video", "width": 1280, "height": 720}])
    info = parse_ffprobe_json(raw)
    assert info.has_video is True
    assert info.has_audio is False


def test_duration_falls_back_to_stream_when_format_lacks_it():
    raw = json.dumps({
        "streams": [
            {"codec_type": "video", "width": 640, "height": 480, "duration": "7.0"},
            {"codec_type": "audio"},
        ],
        "format": {},
    })
    assert parse_ffprobe_json(raw).duration == 7.0


def test_returns_none_on_garbage():
    assert parse_ffprobe_json("not json at all") is None
    assert parse_ffprobe_json("") is None


def test_rotated_video_swaps_dimensions():
    # Вертикальные ролики TikTok/Reels часто приходят с матрицей поворота.
    raw = json.dumps({
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [{"rotation": -90}],
            },
            {"codec_type": "audio"},
        ],
        "format": {"duration": "5.0"},
    })
    info = parse_ffprobe_json(raw)
    assert (info.width, info.height) == (1080, 1920)


@pytest.mark.parametrize(
    "info, expected_substring",
    [
        (None, "не удалось прочитать"),
        (MediaInfo(False, True, 5.0, 0, 0), "видеодорожк"),
        (MediaInfo(True, False, 5.0, 640, 480), "звук"),
        (MediaInfo(True, True, 0.0, 640, 480), "нулевая длительность"),
        (MediaInfo(True, True, 5.0, 0, 0), "разрешение"),
    ],
)
def test_reject_reasons(info, expected_substring):
    reason = video_reject_reason(info)
    assert reason is not None
    assert expected_substring in reason.lower()


def test_good_video_is_accepted():
    assert video_reject_reason(MediaInfo(True, True, 5.0, 1920, 1080)) is None
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_media_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.services.media_probe'`.

- [ ] **Step 3: Создать `bot/services/media_probe.py`**

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

FFPROBE_TIMEOUT = 30


@dataclass(frozen=True)
class MediaInfo:
    has_video: bool
    has_audio: bool
    duration: float
    width: int
    height: int


def _first_float(*values: object) -> float:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _rotation(stream: dict) -> int:
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(side_data["rotation"])
            except (TypeError, ValueError):
                return 0
    return 0


def parse_ffprobe_json(raw: str) -> MediaInfo | None:
    """Разбирает вывод `ffprobe -print_format json`. None — если вывод нечитаем."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = height = 0
    if video is not None:
        width = int(_first_float(video.get("width")))
        height = int(_first_float(video.get("height")))
        # Повёрнутое видео: Telegram ждёт отображаемые размеры, а не размеры кадра.
        if abs(_rotation(video)) % 180 == 90:
            width, height = height, width

    duration = _first_float(
        fmt.get("duration"),
        video.get("duration") if video else None,
        audio.get("duration") if audio else None,
    )

    return MediaInfo(
        has_video=video is not None,
        has_audio=audio is not None,
        duration=duration,
        width=width,
        height=height,
    )


def video_reject_reason(info: MediaInfo | None) -> str | None:
    """Причина, по которой файл нельзя отдавать как видео. None — можно."""
    if info is None:
        return "Не удалось прочитать метаданные файла"
    if not info.has_video:
        return "В файле нет видеодорожки"
    if not info.has_audio:
        return "В файле нет звука"
    if info.duration <= 0:
        return "У файла нулевая длительность"
    if info.width <= 0 or info.height <= 0:
        return "У файла не определяется разрешение"
    return None


async def probe_media(path: Path) -> MediaInfo | None:
    """Запускает ffprobe. Никогда не бросает — при любой беде возвращает None."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams", "-show_format",
        "-print_format", "json",
        str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=FFPROBE_TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logger.warning("ffprobe timeout | path={}", path)
            return None
    except Exception as exc:
        logger.warning("ffprobe failed | path={} error={}", path, exc)
        return None

    return parse_ffprobe_json(stdout.decode(errors="replace"))
```

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_media_probe.py -v`
Expected: PASS, 10 passed.

- [ ] **Step 5: Проверить на настоящем файле**

```bash
docker build --target test -t jw_downloader:test .
docker run --rm jw_downloader:test sh -c '
  ffmpeg -v error -f lavfi -i testsrc=duration=2:size=320x240:rate=10 \
         -f lavfi -i sine=frequency=440:duration=2 \
         -c:v libx264 -c:a aac -shortest /tmp/ok.mp4 &&
  ffmpeg -v error -f lavfi -i testsrc=duration=2:size=320x240:rate=10 \
         -c:v libx264 /tmp/videoonly.mp4 &&
  python -c "
import asyncio
from pathlib import Path
from bot.services.media_probe import probe_media, video_reject_reason
ok = asyncio.run(probe_media(Path(\"/tmp/ok.mp4\")))
bad = asyncio.run(probe_media(Path(\"/tmp/videoonly.mp4\")))
print(\"ok:\", ok, video_reject_reason(ok))
print(\"video-only:\", bad, video_reject_reason(bad))
assert video_reject_reason(ok) is None
assert video_reject_reason(bad) is not None
print(\"OK\")
"'
```
Expected: последняя строка `OK`.

- [ ] **Step 6: Commit**

```bash
git add bot/services/media_probe.py tests/test_media_probe.py
git commit -m "feat(media): add ffprobe integrity gate for video sends"
```

---

### Task 4: Модуль прогресса

Чистые функции форматирования, парсер строк прогресса yt-dlp, троттлящая обёртка над статусным сообщением и оценщик скорости отдачи.

**Files:**
- Create: `bot/services/progress.py`
- Test: `tests/test_progress.py`

**Interfaces:**
- Produces:
  - `PROGRESS_PREFIX = "JWPROG"`, `POSTPROCESS_PREFIX = "JWPP"`
  - `YTDLP_PROGRESS_ARGS: list[str]` — готовые аргументы `--newline` и два `--progress-template` для передачи в командную строку yt-dlp.
  - `DownloadProgress` — frozen dataclass: `status: str`, `downloaded_bytes: int | None`, `total_bytes: int | None`, `speed: float | None`, `eta: int | None`, `files_done: int | None`, свойство `fraction: float | None`. Статус `"gallery"` используется для gallery-dl, у которого структурированного прогресса нет.
  - `parse_progress_line(line: str) -> DownloadProgress | None`
  - `human_bytes(n: float | None) -> str`, `human_duration(seconds: float | None) -> str`, `render_bar(fraction: float | None, width: int = 12) -> str`
  - `render_download_status(p: DownloadProgress) -> str`
  - `render_upload_status(total_bytes: int, elapsed: float, eta: float | None) -> str`
  - `RateTracker` с методами `record(num_bytes: int, seconds: float) -> None`, `estimate_seconds(num_bytes: int) -> float | None` и свойством `rate: float | None`
  - `ProgressReporter(message, min_interval=3.0, clock=time.monotonic)` с `async set(text)` и `async force(text)`
- Потребители: Задача 6 (парсер и аргументы), Задача 10 (репортёр и рендеры), Задача 11 (`RateTracker`).

- [ ] **Step 1: Написать падающий тест `tests/test_progress.py`**

```python
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendMessage

from bot.services.progress import (
    DownloadProgress,
    ProgressReporter,
    RateTracker,
    human_bytes,
    human_duration,
    parse_progress_line,
    render_bar,
    render_download_status,
    render_upload_status,
)

DUMMY_METHOD = SendMessage(chat_id=1, text="x")


class FakeMessage:
    """Подставка вместо aiogram Message: пишет правки в список."""

    def __init__(self, raises=None):
        self.edits: list[str] = []
        self._raises = list(raises or [])

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)
        if self._raises:
            exc = self._raises.pop(0)
            if exc is not None:
                raise exc


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# ── парсер ──

def test_parses_full_download_line():
    line = "JWPROG|downloading|1048576|10485760|NA|524288.0|18"
    p = parse_progress_line(line)
    assert p == DownloadProgress(
        status="downloading",
        downloaded_bytes=1048576,
        total_bytes=10485760,
        speed=524288.0,
        eta=18,
    )
    assert p.fraction == pytest.approx(0.1)


def test_falls_back_to_estimated_total():
    line = "JWPROG|downloading|500|NA|1000|NA|NA"
    p = parse_progress_line(line)
    assert p.total_bytes == 1000
    assert p.speed is None
    assert p.eta is None
    assert p.fraction == pytest.approx(0.5)


def test_fraction_is_none_without_total():
    p = parse_progress_line("JWPROG|downloading|500|NA|NA|NA|NA")
    assert p.fraction is None


def test_parses_postprocess_line():
    p = parse_progress_line("JWPP|started")
    assert p.status == "postprocess"
    assert p.downloaded_bytes is None


def test_ignores_unrelated_output():
    assert parse_progress_line("[youtube] Extracting URL: https://example.com") is None
    assert parse_progress_line("") is None
    assert parse_progress_line("JWPROG|malformed") is None


def test_survives_none_literals_from_ytdlp():
    # yt-dlp может подставить "None" вместо "NA" в зависимости от поля.
    p = parse_progress_line("JWPROG|downloading|10|None|None|None|None")
    assert p.total_bytes is None
    assert p.speed is None


# ── форматирование ──

@pytest.mark.parametrize(
    "value, expected",
    [(None, "—"), (0, "0 Б"), (512, "512 Б"), (1536, "1.5 КБ"),
     (5 * 1024**2, "5.0 МБ"), (2 * 1024**3, "2.00 ГБ")],
)
def test_human_bytes(value, expected):
    assert human_bytes(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [(None, "—"), (5, "5 с"), (75, "1:15"), (3725, "1:02:05")],
)
def test_human_duration(value, expected):
    assert human_duration(value) == expected


def test_render_bar():
    assert render_bar(0.0, width=10) == "░░░░░░░░░░"
    assert render_bar(1.0, width=10) == "██████████"
    assert render_bar(0.5, width=10) == "█████░░░░░"
    # Неизвестная доля даёт пустую строку, а не «нулевой» бар — иначе
    # пользователь решит, что загрузка стоит на месте.
    assert render_bar(None, width=10) == ""


def test_render_download_status_includes_key_numbers():
    p = DownloadProgress("downloading", 1048576, 10485760, 524288.0, 18)
    text = render_download_status(p)
    assert "10%" in text
    assert "МБ" in text
    assert "18 с" in text


def test_render_download_status_without_total():
    p = DownloadProgress("downloading", 1048576, None, None, None)
    text = render_download_status(p)
    assert "1.0 МБ" in text
    assert "%" not in text


def test_render_postprocess_status():
    text = render_download_status(DownloadProgress("postprocess"))
    assert "собира" in text.lower()


def test_render_gallery_status_counts_files():
    text = render_download_status(DownloadProgress("gallery", files_done=3))
    assert "3" in text
    assert "галере" in text.lower()


def test_render_upload_status_hides_estimate_when_unknown():
    text = render_upload_status(1024**3, elapsed=30.0, eta=None)
    assert "1.00 ГБ" in text
    assert "30 с" in text
    assert "≈" not in text


def test_render_upload_status_shows_estimate():
    text = render_upload_status(1024**3, elapsed=30.0, eta=120.0)
    assert "≈" in text
    assert "2:00" in text


# ── оценщик скорости ──

def test_rate_tracker_has_no_estimate_until_it_has_data():
    tracker = RateTracker()
    assert tracker.rate is None
    assert tracker.estimate_seconds(1000) is None


def test_rate_tracker_averages_recent_sends():
    tracker = RateTracker(window=3)
    tracker.record(10_000_000, 10.0)   # 1 МБ/с
    tracker.record(30_000_000, 10.0)   # 3 МБ/с
    assert tracker.rate == pytest.approx(2_000_000)
    assert tracker.estimate_seconds(4_000_000) == pytest.approx(2.0)


def test_rate_tracker_forgets_old_samples():
    tracker = RateTracker(window=2)
    for _ in range(2):
        tracker.record(100, 100.0)     # 1 Б/с
    tracker.record(1000, 1.0)          # 1000 Б/с
    tracker.record(1000, 1.0)
    assert tracker.rate == pytest.approx(1000)


def test_rate_tracker_ignores_nonsense_samples():
    tracker = RateTracker()
    tracker.record(0, 10.0)
    tracker.record(1000, 0.0)
    tracker.record(-5, 1.0)
    assert tracker.rate is None


# ── репортёр ──

async def test_reporter_edits_on_first_call():
    msg = FakeMessage()
    reporter = ProgressReporter(msg, min_interval=3.0, clock=FakeClock())
    await reporter.set("первое")
    assert msg.edits == ["первое"]


async def test_reporter_throttles_rapid_updates():
    msg = FakeMessage()
    clock = FakeClock()
    reporter = ProgressReporter(msg, min_interval=3.0, clock=clock)
    await reporter.set("a")
    clock.now = 1.0
    await reporter.set("b")
    clock.now = 2.9
    await reporter.set("c")
    assert msg.edits == ["a"]
    clock.now = 3.0
    await reporter.set("d")
    assert msg.edits == ["a", "d"]


async def test_reporter_skips_identical_text():
    msg = FakeMessage()
    clock = FakeClock()
    reporter = ProgressReporter(msg, min_interval=0.0, clock=clock)
    await reporter.set("same")
    clock.now = 10.0
    await reporter.set("same")
    assert msg.edits == ["same"]


async def test_force_ignores_throttle_but_not_identity():
    msg = FakeMessage()
    clock = FakeClock()
    reporter = ProgressReporter(msg, min_interval=100.0, clock=clock)
    await reporter.set("a")
    await reporter.force("b")
    assert msg.edits == ["a", "b"]


async def test_reporter_swallows_message_is_not_modified():
    exc = TelegramBadRequest(method=DUMMY_METHOD, message="Bad Request: message is not modified")
    msg = FakeMessage(raises=[exc])
    reporter = ProgressReporter(msg, min_interval=0.0, clock=FakeClock())
    await reporter.set("x")   # не должно бросить


async def test_reporter_swallows_any_exception():
    # Сбой индикации не имеет права ронять загрузку.
    msg = FakeMessage(raises=[RuntimeError("boom"), TelegramRetryAfter(
        method=DUMMY_METHOD, message="Too Many Requests: retry after 5", retry_after=5)])
    reporter = ProgressReporter(msg, min_interval=0.0, clock=FakeClock())
    await reporter.set("x")
    await reporter.set("y")


async def test_reporter_stops_editing_after_message_is_gone():
    # Если сообщение удалено, дальнейшие правки бессмысленны — репортёр
    # должен замолчать, а не долбить API на каждой строке прогресса.
    exc = TelegramBadRequest(method=DUMMY_METHOD, message="Bad Request: message to edit not found")
    msg = FakeMessage(raises=[exc])
    reporter = ProgressReporter(msg, min_interval=0.0, clock=FakeClock())
    await reporter.set("a")
    await reporter.set("b")
    assert msg.edits == ["a"]
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.services.progress'`.

- [ ] **Step 3: Создать `bot/services/progress.py`**

```python
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from aiogram.exceptions import TelegramBadRequest
from loguru import logger

PROGRESS_PREFIX = "JWPROG"
POSTPROCESS_PREFIX = "JWPP"

# Сырые числовые поля, а не *_str-варианты: форматируем сами, чтобы не зависеть
# от локали и версии yt-dlp.
YTDLP_PROGRESS_ARGS: list[str] = [
    "--newline",
    "--progress-template",
    (
        f"download:{PROGRESS_PREFIX}|%(progress.status)s|%(progress.downloaded_bytes)s"
        "|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s"
        "|%(progress.speed)s|%(progress.eta)s"
    ),
    "--progress-template",
    f"postprocess:{POSTPROCESS_PREFIX}|%(progress.status)s",
]

_MISSING = {"NA", "NONE", "", "-"}


def _num(raw: str) -> float | None:
    if raw is None or raw.strip().upper() in _MISSING:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class DownloadProgress:
    status: str
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    # gallery-dl не даёт структурированного прогресса — считаем сохранённые файлы.
    files_done: int | None = None

    @property
    def fraction(self) -> float | None:
        if not self.total_bytes or self.downloaded_bytes is None:
            return None
        return min(1.0, self.downloaded_bytes / self.total_bytes)


def parse_progress_line(line: str) -> DownloadProgress | None:
    """Разбирает одну строку stdout yt-dlp. None — строка не про прогресс."""
    if not line:
        return None
    line = line.strip()

    if line.startswith(POSTPROCESS_PREFIX + "|"):
        return DownloadProgress(status="postprocess")

    if not line.startswith(PROGRESS_PREFIX + "|"):
        return None

    parts = line.split("|")
    if len(parts) != 7:
        return None

    _, status, downloaded, total, total_est, speed, eta = parts
    total_value = _num(total)
    if total_value is None:
        total_value = _num(total_est)
    downloaded_value = _num(downloaded)
    eta_value = _num(eta)

    return DownloadProgress(
        status=status.strip() or "downloading",
        downloaded_bytes=int(downloaded_value) if downloaded_value is not None else None,
        total_bytes=int(total_value) if total_value is not None else None,
        speed=_num(speed),
        eta=int(eta_value) if eta_value is not None else None,
    )


def human_bytes(n: float | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    if n < 1024:
        return f"{int(n)} Б"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} КБ"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} МБ"
    return f"{n / 1024 ** 3:.2f} ГБ"


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} с"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}:{secs:02d}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def render_bar(fraction: float | None, width: int = 12) -> str:
    # Пустая строка при неизвестной доле: «пустой» бар выглядел бы как застой.
    if fraction is None:
        return ""
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "█" * filled + "░" * (width - filled)


def render_download_status(p: DownloadProgress) -> str:
    if p.status == "postprocess":
        return "🎬 <b>Собираю видео и звук…</b>"
    if p.status == "gallery":
        return f"⬇️ <b>Скачиваю галерею…</b>\nФайлов готово: {p.files_done or 0}"

    parts: list[str] = []
    bar = render_bar(p.fraction)
    if bar:
        parts.append(f"{bar}  {int(p.fraction * 100)}%")

    if p.total_bytes:
        parts.append(f"{human_bytes(p.downloaded_bytes)} / {human_bytes(p.total_bytes)}")
    elif p.downloaded_bytes:
        parts.append(human_bytes(p.downloaded_bytes))

    if p.speed:
        parts.append(f"{human_bytes(p.speed)}/с")
    if p.eta:
        parts.append(human_duration(p.eta))

    return "⬇️ <b>Скачиваю</b>\n" + "  ·  ".join(parts)


def render_upload_status(total_bytes: int, elapsed: float, eta: float | None) -> str:
    parts = [human_bytes(total_bytes), f"прошло {human_duration(elapsed)}"]
    if eta is not None:
        parts.append(f"осталось ≈ {human_duration(eta)}")
    return "📤 <b>Отправляю в Telegram</b>\n" + "  ·  ".join(parts)


class RateTracker:
    """Скользящая средняя фактической скорости отдачи.

    Прогресс `сервер → Telegram` изнутри бота не виден, поэтому оценка остатка
    строится на замерах прошлых отправок, а не на константе.
    """

    def __init__(self, window: int = 5) -> None:
        self._samples: deque[tuple[int, float]] = deque(maxlen=window)

    def record(self, num_bytes: int, seconds: float) -> None:
        if num_bytes <= 0 or seconds <= 0:
            return
        self._samples.append((num_bytes, seconds))

    @property
    def rate(self) -> float | None:
        if not self._samples:
            return None
        total_bytes = sum(b for b, _ in self._samples)
        total_seconds = sum(s for _, s in self._samples)
        if total_seconds <= 0:
            return None
        return total_bytes / total_seconds

    def estimate_seconds(self, num_bytes: int) -> float | None:
        rate = self.rate
        if rate is None or rate <= 0:
            return None
        return num_bytes / rate


class ProgressReporter:
    """Троттлящая обёртка над статусным сообщением.

    Никогда не бросает наружу: сбой индикации не должен ронять загрузку.
    """

    def __init__(
        self,
        message,
        min_interval: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._message = message
        self._min_interval = min_interval
        self._clock = clock
        self._last_at: float | None = None
        self._last_text: str | None = None
        self._disabled = False

    async def set(self, text: str) -> None:
        """Обновить статус, если прошёл интервал и текст изменился."""
        now = self._clock()
        if self._last_at is not None and now - self._last_at < self._min_interval:
            return
        await self._edit(text, now)

    async def force(self, text: str) -> None:
        """Обновить немедленно, минуя интервал (смена фазы)."""
        await self._edit(text, self._clock())

    async def _edit(self, text: str, now: float) -> None:
        if self._disabled or text == self._last_text:
            return
        try:
            await self._message.edit_text(text)
        except TelegramBadRequest as exc:
            detail = str(exc).lower()
            if "is not modified" in detail:
                # Не ошибка: просто нечего менять.
                self._last_text = text
                self._last_at = now
                return
            # Сообщение удалено или недоступно — дальше молчим, а не долбим API.
            logger.debug("Progress reporter disabled: {}", exc)
            self._disabled = True
            return
        except Exception as exc:
            logger.debug("Progress update failed, ignoring: {}", exc)
            return
        self._last_text = text
        self._last_at = now
```

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_progress.py -v`
Expected: PASS, все тесты зелёные.

- [ ] **Step 5: Проверить шаблон против настоящего yt-dlp**

Форматы `--progress-template` меняются между версиями, поэтому шаблон проверяем на живой утилите, а не только на синтетических строках.

```bash
docker run --rm jw_downloader:test sh -c '
  python -c "
from bot.services.progress import YTDLP_PROGRESS_ARGS
print(\" \".join(YTDLP_PROGRESS_ARGS))
" &&
  yt-dlp --newline \
    --progress-template "download:JWPROG|%(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s" \
    -f "worst[ext=mp4]/worst" -o /tmp/probe.%\(ext\)s \
    "https://www.w3schools.com/html/mov_bbb.mp4" 2>&1 | grep -c "^JWPROG|"'
```
Expected: ненулевое число строк `JWPROG|`. Если ноль — сверить имена полей с `yt-dlp --help | grep -A20 progress-template` и поправить шаблон **и** тесты.

- [ ] **Step 6: Commit**

```bash
git add bot/services/progress.py tests/test_progress.py
git commit -m "feat(progress): add yt-dlp progress parsing, formatting and throttled reporter"
```

---

### Task 5: Рабочая папка на загрузку и белый список расширений

Сейчас всё качается в одну плоскую директорию, изоляция — только префикс имени, а `uuid` создаётся внутри `download_media` и наружу не возвращается, поэтому хендлер физически не может убрать мусор. Плюс `_find_downloaded_files` отбирает файлы только по `startswith(prefix)`, из-за чего недокачанный `.part` и video-only фрагмент `.f137` уходят пользователю как готовое видео.

**Files:**
- Modify: `bot/services/downloader.py:18` (константа), `:213-226` (`_find_downloaded_files`), `:229-235` (`_cleanup_glob`), `:308-443` (`download_media`), `:238-274` (`_try_gallery_dl`), `:277-305` (`_try_gallery_dl_fallback`)
- Test: `tests/test_downloader_files.py`

**Interfaces:**
- Produces:
  - `MEDIA_EXTS: frozenset[str]` — допустимые расширения результата.
  - `is_media_file(path: Path) -> bool`
  - `_find_downloaded_files(directory: Path, prefix: str) -> list[Path]` — теперь с фильтром.
  - `download_media(url: str, platform: str, work_dir: Path, progress_cb=None) -> DownloadResult` — новый обязательный параметр `work_dir`.
  - `DOWNLOAD_ROOT: Path` вместо `DOWNLOAD_DIR`.
- Потребители: Задачи 6, 8, 10.

- [ ] **Step 1: Написать падающий тест `tests/test_downloader_files.py`**

```python
from pathlib import Path

import pytest

from bot.services.downloader import _find_downloaded_files, is_media_file


def _touch(directory: Path, name: str, size: int = 10) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    return path


def test_rejects_partial_and_intermediate_files(tmp_path):
    prefix = "abc123"
    _touch(tmp_path, f"{prefix}.mp4.part")
    _touch(tmp_path, f"{prefix}.mp4.ytdl")
    _touch(tmp_path, f"{prefix}.f137.mp4")
    _touch(tmp_path, f"{prefix}.f140.m4a")
    good = _touch(tmp_path, f"{prefix}.mp4")

    assert _find_downloaded_files(tmp_path, prefix) == [good]


def test_rejects_unknown_extensions(tmp_path):
    prefix = "abc123"
    _touch(tmp_path, f"{prefix}.txt")
    _touch(tmp_path, f"{prefix}.json")
    _touch(tmp_path, f"{prefix}.description")
    good = _touch(tmp_path, f"{prefix}.mp4")

    assert _find_downloaded_files(tmp_path, prefix) == [good]


def test_rejects_zero_size_files(tmp_path):
    prefix = "abc123"
    (tmp_path / f"{prefix}_1.jpg").write_bytes(b"")
    good = _touch(tmp_path, f"{prefix}_2.jpg")

    assert _find_downloaded_files(tmp_path, prefix) == [good]


def test_ignores_other_prefixes(tmp_path):
    mine = _touch(tmp_path, "mine.mp4")
    _touch(tmp_path, "theirs.mp4")

    assert _find_downloaded_files(tmp_path, "mine") == [mine]


def test_carousel_sorts_numerically_not_lexically(tmp_path):
    prefix = "abc"
    for n in (10, 2, 1):
        _touch(tmp_path, f"{prefix}_{n}.jpg")

    names = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert names == [f"{prefix}_1.jpg", f"{prefix}_2.jpg", f"{prefix}_10.jpg"]


def test_unnumbered_item_does_not_jump_ahead_of_numbered_ones(tmp_path):
    # Регрессия: gallery-dl отдаёт "_NA.mp4", который лексически вставал
    # впереди "_1.jpg" и ломал порядок карусели.
    prefix = "abc"
    _touch(tmp_path, f"{prefix}_1.jpg")
    _touch(tmp_path, f"{prefix}_2.jpg")
    _touch(tmp_path, f"{prefix}_NA.mp4")

    names = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert names[0] == f"{prefix}_1.jpg"
    assert names[1] == f"{prefix}_2.jpg"


def test_missing_directory_returns_empty(tmp_path):
    assert _find_downloaded_files(tmp_path / "nope", "abc") == []


@pytest.mark.parametrize(
    "name, expected",
    [("a.mp4", True), ("a.jpg", True), ("a.webp", True), ("a.mkv", True),
     ("a.webm", True), ("a.m4a", False), ("a.mp4.part", False),
     ("a.f137.mp4", False), ("a.ytdl", False), ("a", False)],
)
def test_is_media_file(tmp_path, name, expected):
    assert is_media_file(tmp_path / name) is expected
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_downloader_files.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_media_file'`.

- [ ] **Step 3: Заменить фильтрацию в `bot/services/downloader.py`**

Заменить константу на строке 18:

```python
DOWNLOAD_ROOT = Path(settings.DOWNLOAD_ROOT)
```

Рядом с `IMAGE_EXTS` добавить:

```python
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov"}
MEDIA_EXTS = frozenset(IMAGE_EXTS | VIDEO_EXTS)
# yt-dlp оставляет рядом с результатом промежуточные файлы. Раньше они проходили
# отбор по одному лишь префиксу имени и уходили пользователю как готовое видео.
_REJECT_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp")
_FORMAT_FRAGMENT_RE = re.compile(r"\.f\d+\.[A-Za-z0-9]+$")
```

Добавить `import re` в блок импортов.

Заменить `_find_downloaded_files` (строки 213-226) на:

```python
def is_media_file(path: Path) -> bool:
    """Годится ли файл к отправке пользователю.

    Отсекает недокачанные `.part`, служебные `.ytdl` и раздельные DASH-дорожки
    вида `<name>.f137.mp4`, которые до этого уходили как готовое видео.
    """
    name = path.name
    if name.endswith(_REJECT_SUFFIXES):
        return False
    if _FORMAT_FRAGMENT_RE.search(name):
        return False
    return path.suffix.lower() in MEDIA_EXTS


def _find_downloaded_files(directory: Path, prefix: str) -> list[Path]:
    result: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []

    for f in entries:
        if not f.name.startswith(prefix) or not is_media_file(f):
            continue
        try:
            if f.stat().st_size <= 0:
                continue
        except OSError:
            continue
        result.append(f)

    def _sort_key(p: Path) -> tuple[int, str]:
        # Нумерованные элементы карусели идут по возрастанию номера, всё
        # остальное — после них, иначе "_NA.mp4" лексически встаёт перед "_1.jpg".
        rest = p.name[len(prefix):]
        m = re.search(r"_(\d+)", rest)
        return (int(m.group(1)) if m else 10 ** 9, p.name)

    return sorted(result, key=_sort_key)
```

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_downloader_files.py -v`
Expected: PASS.

- [ ] **Step 5: Перевести `download_media` на рабочую папку**

В сигнатуре:

```python
async def download_media(url: str, platform: str, work_dir: Path) -> DownloadResult:
```

Внутри, вместо `DOWNLOAD_DIR.mkdir(...)` и `output_path = DOWNLOAD_DIR / ...`:

```python
    work_dir.mkdir(parents=True, exist_ok=True)

    filename = uuid4().hex
    output_path = work_dir / (filename + ".%(ext)s")
```

Обратите внимание: жёсткий `.mp4` в шаблоне заменён на `.%(ext)s`, чтобы имя
соответствовало фактическому контейнеру.

Далее по всему телу функции заменить каждое вхождение `DOWNLOAD_DIR` на `work_dir`.
Это затрагивает: `_try_gallery_dl_fallback`, `_cleanup_glob`, `_find_downloaded_files`,
а также ветки шаблонов для instagram и pinterest.

Функции `_try_gallery_dl(url, filename)` и `_try_gallery_dl_fallback(url, filename)`
получают дополнительный первый параметр `work_dir: Path` и используют его вместо
`DOWNLOAD_DIR`.

**Все шесть вызовов `_cleanup_glob` удалить.** Уборкой теперь владеет хендлер
(Задача 10): он создаёт `work_dir` и в `finally` делает `shutil.rmtree`. Саму
функцию `_cleanup_glob` удалить целиком.

Строку с ошибкой таймаута исправить, убрав захардкоженное «120»:

```python
                    return DownloadResult(
                        success=False,
                        error_message=(
                            f"⏱ Таймаут: сервер не ответил за {settings.DOWNLOAD_TIMEOUT} секунд"
                        ),
                    )
```

- [ ] **Step 6: Убедиться, что модуль импортируется и старые тесты живы**

Run: `./scripts/test.sh -v`
Expected: PASS, все тесты.

- [ ] **Step 7: Проверить, что `_cleanup_glob` и `DOWNLOAD_DIR` исчезли**

```bash
grep -rn "_cleanup_glob\|DOWNLOAD_DIR" bot/ || echo "чисто"
```
Expected: `чисто`.

- [ ] **Step 8: Commit**

```bash
git add bot/services/downloader.py tests/test_downloader_files.py
git commit -m "feat(downloader): per-download work dir and media extension whitelist"
```

---

### Task 6: Потоковый запуск подпроцесса с прогрессом и убийством группы

Сейчас `process.communicate()` буферизует stdout до завершения процесса — прогресса не видно **в принципе**. Плюс `process.kill()` убивает только прямого потомка, и осиротевший ffmpeg продолжает жить.

**Files:**
- Create: `bot/services/proc.py`
- Modify: `bot/services/downloader.py` (заменить оба вызова `communicate()` — для yt-dlp и для gallery-dl)
- Test: `tests/test_proc.py`

**Interfaces:**
- Produces:
  - `ProcResult` — frozen dataclass: `returncode: int`, `stdout: str`, `stderr: str`.
  - `async run_streaming(cmd, timeout, on_stdout_line=None) -> ProcResult` — бросает `asyncio.TimeoutError` по таймауту, предварительно убив **группу** процессов. stdout читается построчно (для прогресса), stderr — кусками (чтобы длинная строка не упёрлась в лимит `StreamReader`).
- Потребители: Задача 6 (сам downloader), косвенно Задача 10.

- [ ] **Step 1: Написать падающий тест `tests/test_proc.py`**

```python
import asyncio
import sys

import pytest

from bot.services.proc import ProcResult, run_streaming


async def test_captures_output_and_returncode():
    result = await run_streaming(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        timeout=30,
    )
    assert isinstance(result, ProcResult)
    assert result.returncode == 0
    assert "out" in result.stdout
    assert "err" in result.stderr


async def test_streams_lines_as_they_appear():
    # Ключевое отличие от communicate(): строки должны приходить ДО завершения.
    seen: list[str] = []
    done = asyncio.Event()

    def on_line(line: str) -> None:
        seen.append(line)
        if len(seen) >= 2:
            done.set()

    task = asyncio.create_task(
        run_streaming(
            [sys.executable, "-u", "-c",
             "import time\nfor i in range(5):\n    print(i, flush=True)\n    time.sleep(0.4)"],
            timeout=30,
            on_stdout_line=on_line,
        )
    )
    await asyncio.wait_for(done.wait(), timeout=5)
    assert not task.done(), "строки пришли только после завершения процесса"
    result = await task
    assert result.returncode == 0


async def test_large_stderr_does_not_deadlock():
    # Если stderr не сливать параллельно, буфер пайпа переполнится и всё встанет.
    result = await run_streaming(
        [sys.executable, "-c",
         "import sys; sys.stderr.write('x' * 500000); sys.stdout.write('done')"],
        timeout=30,
    )
    assert result.stdout == "done"
    assert len(result.stderr) == 500000


async def test_timeout_raises():
    with pytest.raises(asyncio.TimeoutError):
        await run_streaming([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)


async def test_timeout_kills_the_whole_process_group(tmp_path):
    # Регрессия: yt-dlp порождает ffmpeg, и убийство только прямого потомка
    # оставляло его сиротой, дожирающим CPU и диск.
    marker = tmp_path / "grandchild_survived"
    script = (
        f"import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', "
        f"\"import time; time.sleep(3); open(r'{marker}', 'w').write('alive')\"])\n"
        f"time.sleep(30)\n"
    )
    with pytest.raises(asyncio.TimeoutError):
        await run_streaming([sys.executable, "-c", script], timeout=1)

    await asyncio.sleep(4)
    assert not marker.exists(), "внук пережил таймаут — группа процессов не убита"


async def test_callback_failure_does_not_break_the_run():
    def boom(line: str) -> None:
        raise RuntimeError("callback exploded")

    result = await run_streaming(
        [sys.executable, "-c", "print('still fine')"], timeout=30, on_stdout_line=boom
    )
    assert result.returncode == 0
    assert "still fine" in result.stdout
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_proc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.services.proc'`.

- [ ] **Step 3: Создать `bot/services/proc.py`**

```python
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True)
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


# Строки прогресса короткие, но лимит поднят с дефолтных 64 КБ: длинная строка
# без перевода каретки иначе роняет readline() исключением.
STREAM_LIMIT = 1024 * 1024


async def _drain_lines(
    stream: asyncio.StreamReader | None,
    sink: list[str],
    on_line: Callable[[str], None] | None = None,
) -> None:
    if stream is None:
        return
    while True:
        try:
            raw = await stream.readline()
        except (ValueError, asyncio.LimitOverrunError):
            # Строка длиннее лимита — дочитываем куском и продолжаем.
            raw = await stream.read(STREAM_LIMIT)
        if not raw:
            break
        line = raw.decode(errors="replace")
        sink.append(line)
        if on_line is None:
            continue
        try:
            on_line(line.rstrip("\r\n"))
        except Exception as exc:
            # Сбой обработчика прогресса не имеет права ронять загрузку.
            logger.debug("stdout callback failed, ignoring: {}", exc)


async def _drain_all(stream: asyncio.StreamReader | None, sink: list[str]) -> None:
    """Сливает поток кусками, не полагаясь на переводы строк.

    Для stderr это обязательно: yt-dlp умеет выдать сотни килобайт одной
    строкой, а `readline()` на таком объёме бросает исключение по лимиту.
    """
    if stream is None:
        return
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        sink.append(chunk.decode(errors="replace"))


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """Убивает всю группу процессов.

    yt-dlp порождает ffmpeg; `process.kill()` убил бы только прямого потомка
    и оставил внука сиротой.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        return
    with contextlib.suppress(ProcessLookupError):
        process.kill()


async def run_streaming(
    cmd: list[str],
    timeout: float,
    on_stdout_line: Callable[[str], None] | None = None,
) -> ProcResult:
    """Запускает команду, отдавая строки stdout по мере появления.

    В отличие от `process.communicate()`, не буферизует вывод до завершения —
    именно поэтому возможна индикация прогресса. stderr сливается параллельно,
    иначе переполнение буфера пайпа даст дедлок.

    По таймауту убивает группу процессов и бросает `asyncio.TimeoutError`.
    """
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,   # своя группа процессов, чтобы убить всё разом
        limit=STREAM_LIMIT,
    )

    out_chunks: list[str] = []
    err_chunks: list[str] = []
    readers = asyncio.gather(
        _drain_lines(process.stdout, out_chunks, on_stdout_line),
        _drain_all(process.stderr, err_chunks),
    )

    try:
        await asyncio.wait_for(readers, timeout=timeout)
        returncode = await asyncio.wait_for(process.wait(), timeout=30)
    except BaseException:
        # Сюда попадают и таймаут, и CancelledError при остановке бота.
        # В обоих случаях нельзя оставить осиротевший ffmpeg.
        _kill_group(process)
        readers.cancel()
        with contextlib.suppress(BaseException):
            await readers
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(process.wait(), timeout=10)
        raise

    return ProcResult(
        returncode=returncode,
        stdout="".join(out_chunks),
        stderr="".join(err_chunks),
    )
```

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_proc.py -v`
Expected: PASS, 6 passed.

- [ ] **Step 5: Перевести `downloader.py` на `run_streaming`**

Добавить импорты:

```python
from collections.abc import Awaitable, Callable

from bot.services.proc import run_streaming
from bot.services.progress import YTDLP_PROGRESS_ARGS, DownloadProgress, parse_progress_line
```

`download_media` получает четвёртый параметр:

```python
async def download_media(
    url: str,
    platform: str,
    work_dir: Path,
    progress_cb: Callable[[DownloadProgress], Awaitable[None]] | None = None,
) -> DownloadResult:
```

Внутри, перед циклом попыток, завести мост из синхронного колбэка в корутину.
`run_streaming` вызывает обработчик синхронно, а отправка правки в Telegram —
асинхронная, поэтому создаём задачу и не ждём её:

```python
    pending: set[asyncio.Task] = set()

    def _on_line(line: str) -> None:
        if progress_cb is None:
            return
        parsed = parse_progress_line(line)
        if parsed is None:
            return
        task = asyncio.create_task(progress_cb(parsed))
        pending.add(task)
        task.add_done_callback(pending.discard)
```

Заменить блок запуска yt-dlp (сейчас `create_subprocess_exec` + `wait_for(communicate())`) на:

```python
                try:
                    proc_result = await run_streaming(
                        cmd, timeout=settings.DOWNLOAD_TIMEOUT, on_stdout_line=_on_line
                    )
                except asyncio.TimeoutError:
                    logger.warning("Download timeout | url={}", url)
                    return DownloadResult(
                        success=False,
                        error_message=(
                            f"⏱ Таймаут: сервер не ответил за {settings.DOWNLOAD_TIMEOUT} секунд"
                        ),
                    )
                stdout = proc_result.stdout
                stderr = proc_result.stderr
                returncode = proc_result.returncode
```

Дальше по функции заменить `stderr.decode(errors="replace")` на `stderr` и
`stdout.decode(errors="replace")` на `stdout` — они уже строки. Заменить
`process.returncode` на `returncode`.

В `_try_gallery_dl` сделать то же самое, но со своим счётчиком: структурированного
прогресса gallery-dl не даёт, поэтому считаем сохранённые файлы по строкам вывода —
он печатает путь каждого записанного файла.

```python
    gd_done = 0

    def _on_gd_line(line: str) -> None:
        nonlocal gd_done
        # gallery-dl печатает абсолютный путь каждого сохранённого файла.
        if not line.startswith("/"):
            return
        gd_done += 1
        if progress_cb is None:
            return
        task = asyncio.create_task(
            progress_cb(DownloadProgress(status="gallery", files_done=gd_done))
        )
        pending.add(task)
        task.add_done_callback(pending.discard)

    try:
        proc_result = await run_streaming(
            cmd, timeout=settings.DOWNLOAD_TIMEOUT, on_stdout_line=_on_gd_line
        )
    except asyncio.TimeoutError:
        logger.warning("gallery-dl timeout | url={}", url)
        return None
```

`_try_gallery_dl` и `_try_gallery_dl_fallback` получают параметры `progress_cb` и
`pending`, чтобы счётчик доходил до пользователя и на приоритетном пути gallery-dl
(Pinterest, Instagram `/p/`, TikTok `/photo/`), а не только на fallback.

В конце `download_media`, перед возвратом любого результата, дождаться висящих
задач прогресса, чтобы они не пережили запрос:

```python
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
```

Проще всего обернуть тело функции в `try/finally` с этим ожиданием в `finally`.

Добавить `YTDLP_PROGRESS_ARGS` в базовый блок `_build_command` (см. Задачу 7).

- [ ] **Step 6: Прогнать все тесты**

Run: `./scripts/test.sh -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bot/services/proc.py bot/services/downloader.py tests/test_proc.py
git commit -m "feat(downloader): stream subprocess output for progress, kill process groups on timeout"
```

---

### Task 7: Селекторы форматов — максимальное качество

Пин клиентов YouTube даёт замеренные 360p. Арифметика `MAX-5`/`MAX-2`/`MAX-15` была настроена под 50 МБ и при 1500 теряет смысл.

**Files:**
- Modify: `bot/services/downloader.py:102-210` (`_build_command`)
- Test: `tests/test_build_command.py`

**Interfaces:**
- Consumes: `YTDLP_PROGRESS_ARGS` из Задачи 4.
- Produces: `_build_command(url, platform, output_path, cookies_path) -> list[str]` (сигнатура прежняя).

- [ ] **Step 1: Написать падающий тест `tests/test_build_command.py`**

```python
from pathlib import Path

import pytest

from bot.config import settings
from bot.services.downloader import _build_command


def _cmd(platform: str, url: str = "https://example.com/x") -> list[str]:
    return _build_command(url, platform, Path("/srv/jw_downloads/w/out.%(ext)s"), None)


def _selector(cmd: list[str]) -> str:
    return cmd[cmd.index("-f") + 1]


@pytest.mark.parametrize(
    "platform", ["youtube", "instagram", "tiktok", "facebook", "pinterest", "other"]
)
def test_progress_template_is_always_present(platform):
    cmd = _cmd(platform)
    assert "--newline" in cmd
    assert cmd.count("--progress-template") == 2


@pytest.mark.parametrize(
    "platform", ["youtube", "instagram", "tiktok", "facebook", "pinterest", "other"]
)
def test_url_is_last_argument(platform):
    assert _cmd(platform)[-1] == "https://example.com/x"


def test_max_filesize_follows_settings():
    cmd = _cmd("youtube")
    assert cmd[cmd.index("--max-filesize") + 1] == f"{settings.MAX_FILE_SIZE_MB}M"


def test_youtube_client_pin_is_gone():
    # Замерено: пин player_client давал жёсткие 360p.
    cmd = _cmd("youtube")
    assert "player_client" not in " ".join(cmd)
    assert "manifest-filesize-approx" not in " ".join(cmd)


def test_youtube_selector_asks_for_best_video_plus_audio():
    selector = _selector(_cmd("youtube"))
    assert selector.startswith("bv*")
    assert "+ba" in selector
    # Никакой арифметики от лимита: она была нужна только для 50 МБ.
    assert "filesize<" not in selector


def test_facebook_prefers_h264_over_av1():
    # Регрессия: AV1 у Facebook даёт «звук без картинки» в плеере Telegram.
    selector = _selector(_cmd("facebook"))
    assert "avc1" in selector
    first_avc1 = selector.index("avc1")
    assert "av01" not in selector[:first_avc1]


def test_facebook_prefers_hd_over_sd():
    selector = _selector(_cmd("facebook"))
    assert "/hd/" in selector or selector.endswith("/hd") or "hd/sd" in selector


def test_tiktok_excludes_watermarked_formats():
    # download / download_addr — дорожки с водяным знаком.
    selector = _selector(_cmd("tiktok"))
    assert "format_id!=download" in selector
    assert "format_id!=download_addr" in selector


def test_tiktok_keeps_proxy_support(monkeypatch):
    monkeypatch.setattr(settings, "TIKTOK_PROXY", "http://proxy:8080")
    cmd = _cmd("tiktok")
    assert cmd[cmd.index("--proxy") + 1] == "http://proxy:8080"


def test_cookies_are_passed_when_present():
    cmd = _build_command("https://x", "instagram", Path("/tmp/o.%(ext)s"), Path("/tmp/c.txt"))
    assert cmd[cmd.index("--cookies") + 1] == "/tmp/c.txt"
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_build_command.py -v`
Expected: FAIL на `test_youtube_client_pin_is_gone` и `test_progress_template_is_always_present`.

- [ ] **Step 3: Переписать базовый блок и ветки в `_build_command`**

Базовый блок (сейчас строки 103-111) — добавить аргументы прогресса:

```python
    cmd = [
        "yt-dlp",
        "--no-check-certificates",
        "--socket-timeout", "30",
        "--retries", "3",
        "--age-limit", "99",
        "--max-filesize", f"{settings.MAX_FILE_SIZE_MB}M",
        "-o", str(output_path),
        *YTDLP_PROGRESS_ARGS,
    ]
```

Ветка **youtube** — удалить обе строки с `--compat-options manifest-filesize-approx`
и `--extractor-args youtube:player_client=...`, а каскад селекторов заменить на:

```python
    elif platform == "youtube":
        cmd.extend(["--no-playlist"])
        # avc1+m4a первым: плеер Telegram воспроизводит его без сюрпризов.
        # vp9/opus — запасной, av01 последним, потому что декодируется хуже всего.
        cmd.extend([
            "-f",
            "bv*[vcodec^=avc1]+ba[ext=m4a]/"
            "bv*[vcodec^=vp9]+ba/"
            "bv*+ba/"
            "b[ext=mp4]/b",
            "--merge-output-format", "mp4",
        ])
```

Ветка **facebook** — заменить размерный каскад на:

```python
    elif platform == "facebook":
        cmd.extend(["--no-playlist"])
        # H.264 строго раньше AV1: на AV1 плеер Telegram даёт «звук без картинки».
        cmd.extend([
            "-f",
            "bv*[vcodec^=avc1]+ba[ext=m4a]/"
            "b[vcodec^=avc1]/"
            "hd/sd/"
            "b[ext=mp4]/b",
            "--merge-output-format", "mp4",
        ])
        cmd.extend(["--add-header", ua, "--add-header", accept_lang])
```

Ветки **instagram**, **tiktok**, **pinterest** и `else` оставить как есть —
у них нет привязки к `MAX_FILE_SIZE_MB` и они уже настроены верно.

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_build_command.py -v`
Expected: PASS, 12 passed.

- [ ] **Step 5: Проверить YouTube вживую**

```bash
docker run --rm jw_downloader:test python -c "
import asyncio, tempfile
from pathlib import Path
from bot.services.downloader import download_media
d = Path(tempfile.mkdtemp())
r = asyncio.run(download_media('https://www.youtube.com/watch?v=aqz-KE-bpKQ', 'youtube', d))
print(r.success, r.file_size_mb, r.error_message)
import subprocess
if r.file_path:
    print(subprocess.run(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=width,height','-of','csv=p=0', r.file_path],
        capture_output=True, text=True).stdout)
"
```
Expected: `success=True` и разрешение **заметно выше 640x360**. Если по-прежнему 360p — не двигаться дальше, а разбираться с `yt-dlp -F` на этом URL.

- [ ] **Step 6: Commit**

```bash
git add bot/services/downloader.py tests/test_build_command.py
git commit -m "feat(downloader): unpin YouTube clients and rewrite selectors for max quality"
```

---

### Task 8: Живучий подметальщик

`stat()` стоит вне `try`, поэтому одна гонка с уборкой хендлера убивает задачу навсегда и молча. Обход не рекурсивен, так что новые подпапки он бы просто не заметил. Порог в 10 минут меньше худшего времени отдачи, то есть подметальщик способен удалить файл прямо во время отправки.

**Files:**
- Modify: `bot/services/cleanup.py` (целиком)
- Modify: `bot/__main__.py:35`
- Test: `tests/test_cleanup.py`

**Interfaces:**
- Produces:
  - `DOWNLOAD_ROOT: Path`
  - `active_dirs: set[Path]` и контекстный менеджер `reserve_dir(path: Path)`
  - `sweep_once(root: Path, max_age_sec: float, protected: set[Path], now: float) -> int`
  - `async periodic_cleanup(interval_minutes: int = 5, max_age_minutes: int | None = None) -> None`
  - `async remove_file(path)` — сохраняется, используется существующим кодом.
- Потребители: Задачи 10 и 12.

- [ ] **Step 1: Написать падающий тест `tests/test_cleanup.py`**

```python
import os
import time
from pathlib import Path

from bot.services.cleanup import reserve_dir, sweep_once


def _aged_dir(root: Path, name: str, age_sec: float) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"x" * 100)
    old = time.time() - age_sec
    os.utime(d / "video.mp4", (old, old))
    os.utime(d, (old, old))
    return d


def test_removes_orphaned_directories(tmp_path):
    old = _aged_dir(tmp_path, "old", age_sec=3600)
    fresh = _aged_dir(tmp_path, "fresh", age_sec=10)

    removed = sweep_once(tmp_path, max_age_sec=1800, protected=set(), now=time.time())

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_never_touches_protected_dirs_regardless_of_age(tmp_path):
    # Регрессия: подметальщик мог удалить файл прямо во время отдачи.
    active = _aged_dir(tmp_path, "active", age_sec=99999)

    removed = sweep_once(tmp_path, max_age_sec=1, protected={active}, now=time.time())

    assert removed == 0
    assert active.exists()


def test_removes_stray_top_level_files(tmp_path):
    stray = tmp_path / "leftover.mp4"
    stray.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(stray, (old, old))

    assert sweep_once(tmp_path, max_age_sec=1800, protected=set(), now=time.time()) == 1
    assert not stray.exists()


def test_survives_entry_vanishing_mid_sweep(tmp_path, monkeypatch):
    # Регрессия: stat() стоял вне try, и гонка с хендлером убивала задачу навсегда.
    _aged_dir(tmp_path, "a", age_sec=3600)
    victim = _aged_dir(tmp_path, "b", age_sec=3600)

    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == victim:
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    removed = sweep_once(tmp_path, max_age_sec=1800, protected=set(), now=time.time())
    assert removed == 1


def test_missing_root_is_not_an_error(tmp_path):
    assert sweep_once(tmp_path / "nope", max_age_sec=1, protected=set(), now=time.time()) == 0


def test_reserve_dir_registers_and_releases(tmp_path):
    from bot.services.cleanup import active_dirs

    d = tmp_path / "work"
    assert d not in active_dirs
    with reserve_dir(d):
        assert d in active_dirs
    assert d not in active_dirs


def test_reserve_dir_releases_on_exception(tmp_path):
    from bot.services.cleanup import active_dirs

    d = tmp_path / "work"
    try:
        with reserve_dir(d):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert d not in active_dirs
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_cleanup.py -v`
Expected: FAIL — `ImportError: cannot import name 'sweep_once'`.

- [ ] **Step 3: Переписать `bot/services/cleanup.py` целиком**

```python
from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from bot.config import settings

DOWNLOAD_ROOT = Path(settings.DOWNLOAD_ROOT)

# Рабочие папки, занятые прямо сейчас. Подметальщик не трогает их ни при каком
# возрасте: отдача гигабайтного файла легко переживает любой разумный порог.
active_dirs: set[Path] = set()


@contextlib.contextmanager
def reserve_dir(path: Path) -> Iterator[Path]:
    """Помечает папку занятой на время загрузки и отдачи."""
    active_dirs.add(path)
    try:
        yield path
    finally:
        active_dirs.discard(path)


async def remove_file(path: str | Path) -> None:
    try:
        os.unlink(path)
        logger.info("File removed: {}", path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.error("Failed to remove file {}: {}", path, exc)


def _remove(entry: Path) -> None:
    if entry.is_dir():
        shutil.rmtree(entry, ignore_errors=True)
    else:
        entry.unlink(missing_ok=True)


def sweep_once(root: Path, max_age_sec: float, protected: set[Path], now: float) -> int:
    """Удаляет осиротевшие записи. Никогда не бросает."""
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0

    removed = 0
    for entry in entries:
        try:
            if entry in protected:
                continue
            # stat() внутри try: запись может исчезнуть между iterdir() и сюда,
            # и раньше это навсегда убивало фоновую задачу.
            if now - entry.stat().st_mtime <= max_age_sec:
                continue
            _remove(entry)
            removed += 1
        except OSError:
            continue
        except Exception as exc:
            logger.error("Cleanup failed for {}: {}", entry, exc)
            continue

    return removed


async def periodic_cleanup(interval_minutes: int = 5, max_age_minutes: int | None = None) -> None:
    """Страховка от осиротевших файлов. Цикл не должен умирать никогда."""
    max_age_sec = (max_age_minutes or settings.CLEANUP_MAX_AGE_MIN) * 60

    while True:
        try:
            await asyncio.sleep(interval_minutes * 60)
            removed = sweep_once(DOWNLOAD_ROOT, max_age_sec, active_dirs, time.time())
            if removed:
                logger.info("Periodic cleanup: removed {} orphaned entr(ies)", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Periodic cleanup iteration failed, continuing: {}", exc)
```

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_cleanup.py -v`
Expected: PASS, 7 passed.

- [ ] **Step 5: Надзор за фоновой задачей в `bot/__main__.py`**

Заменить строку 35 на:

```python
    def _supervise_cleanup(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            logger.warning("Cleanup task exited unexpectedly, restarting")
        else:
            logger.exception("Cleanup task crashed, restarting: {}", exc)
        # Без перезапуска страховка от осиротевших файлов исчезала навсегда.
        new_task = asyncio.create_task(periodic_cleanup())
        new_task.add_done_callback(_supervise_cleanup)

    _cleanup_task = asyncio.create_task(periodic_cleanup())  # noqa: F841
    _cleanup_task.add_done_callback(_supervise_cleanup)
```

- [ ] **Step 6: Прогнать все тесты**

Run: `./scripts/test.sh -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bot/services/cleanup.py bot/__main__.py tests/test_cleanup.py
git commit -m "fix(cleanup): recursive resilient sweeper with active-dir registry and supervision"
```

---

### Task 9: Логика отправки — размер, `file://` и трактовка ошибок

`TelegramEntityTooLarge` наследует `TelegramNetworkError`, поэтому сейчас оверсайз-файл переотправляется четыре раза. При 1500 МБ это уже не «медленно», а катастрофа. Плюс отправка, упершаяся в серверные 500 секунд, **всё равно доставляет файл** — ретрай дал бы дубль в чате.

**Files:**
- Create: `bot/services/sending.py`
- Test: `tests/test_sending.py`

**Interfaces:**
- Produces:
  - `SERVER_IDLE_TIMEOUT = 500`, `PROBABLY_DELIVERED_AFTER = 400.0`
  - `SendVerdict` — enum: `RETRY`, `GIVE_UP`, `PROBABLY_DELIVERED`, `TOO_LARGE`
  - `classify_send_failure(exc, elapsed, attempt, max_attempts) -> SendVerdict`
  - `sanitize_filename(name: str) -> str`
  - `to_file_uri(path: Path) -> str`
  - `oversize_reason(path: Path, limit_mb: int) -> str | None`
- Потребители: Задача 10.

- [ ] **Step 1: Написать падающий тест `tests/test_sending.py`**

```python
import asyncio
from pathlib import Path

import aiohttp
import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage

from bot.services.sending import (
    PROBABLY_DELIVERED_AFTER,
    SendVerdict,
    classify_send_failure,
    oversize_reason,
    sanitize_filename,
    to_file_uri,
)

M = SendMessage(chat_id=1, text="x")


# ── трактовка ошибок ──

def test_too_large_is_never_retried():
    # Регрессия: TelegramEntityTooLarge наследует TelegramNetworkError,
    # поэтому попадал в сетевую ветку и переотправлялся 4 раза.
    exc = TelegramEntityTooLarge(method=M, message="Request Entity Too Large")
    assert classify_send_failure(exc, elapsed=1.0, attempt=0, max_attempts=4) == SendVerdict.TOO_LARGE


def test_long_send_failure_is_treated_as_probably_delivered():
    # У telegram-bot-api жёсткий IDLE_TIMEOUT=500 с; файл при этом доезжает,
    # теряется только ответ. Ретрай дал бы дубль в чате.
    exc = TelegramNetworkError(method=M, message="Server disconnected")
    verdict = classify_send_failure(
        exc, elapsed=PROBABLY_DELIVERED_AFTER + 1, attempt=0, max_attempts=4
    )
    assert verdict == SendVerdict.PROBABLY_DELIVERED


def test_quick_network_error_is_retried():
    exc = TelegramNetworkError(method=M, message="Connection reset")
    assert classify_send_failure(exc, elapsed=2.0, attempt=0, max_attempts=4) == SendVerdict.RETRY


def test_network_error_gives_up_after_last_attempt():
    exc = TelegramNetworkError(method=M, message="Connection reset")
    assert classify_send_failure(exc, elapsed=2.0, attempt=3, max_attempts=4) == SendVerdict.GIVE_UP


@pytest.mark.parametrize(
    "exc",
    [asyncio.TimeoutError(), aiohttp.ClientError("boom")],
)
def test_transport_errors_are_retried(exc):
    assert classify_send_failure(exc, elapsed=1.0, attempt=0, max_attempts=4) == SendVerdict.RETRY


@pytest.mark.parametrize("exc", [asyncio.TimeoutError(), aiohttp.ClientError("boom")])
def test_transport_errors_also_respect_probably_delivered(exc):
    verdict = classify_send_failure(exc, elapsed=450.0, attempt=0, max_attempts=4)
    assert verdict == SendVerdict.PROBABLY_DELIVERED


def test_retry_after_is_retried():
    exc = TelegramRetryAfter(method=M, message="Too Many Requests", retry_after=3)
    assert classify_send_failure(exc, elapsed=1.0, attempt=0, max_attempts=4) == SendVerdict.RETRY


def test_bad_request_is_not_retried():
    exc = TelegramBadRequest(method=M, message="Bad Request: wrong file identifier")
    assert classify_send_failure(exc, elapsed=1.0, attempt=0, max_attempts=4) == SendVerdict.GIVE_UP


def test_unknown_error_is_not_retried():
    assert classify_send_failure(ValueError("?"), elapsed=1.0, attempt=0, max_attempts=4) == SendVerdict.GIVE_UP


# ── file:// URI ──

def test_plain_path_becomes_file_uri():
    assert to_file_uri(Path("/srv/jw_downloads/ab/video.mp4")) == "file:///srv/jw_downloads/ab/video.mp4"


def test_percent_is_double_encoded():
    # Сервер декодирует аргумент формы дважды: сначала HTTP-слой, потом
    # get_local_file_path. Литеральный '%' обязан приехать как '%2525'.
    assert to_file_uri(Path("/srv/a%b.mp4")) == "file:///srv/a%2525b.mp4"


def test_space_is_double_encoded():
    assert to_file_uri(Path("/srv/a b.mp4")) == "file:///srv/a%2520b.mp4"


# ── санитизация имён ──

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("clip.mp4", "clip.mp4"),
        ("my video (1).mp4", "my_video_1.mp4"),
        ("100%_готово.mp4", "100_.mp4"),
        ("...mp4", "mp4"),
        ("", "file"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitized_name_is_uri_safe():
    name = sanitize_filename("странное имя 100% (копия).mp4")
    assert to_file_uri(Path("/srv") / name) == f"file:///srv/{name}"


# ── гейт размера ──

def test_oversize_reason_none_for_small_file(tmp_path):
    f = tmp_path / "small.mp4"
    f.write_bytes(b"x" * 1024)
    assert oversize_reason(f, limit_mb=10) is None


def test_oversize_reason_reports_both_numbers(tmp_path):
    f = tmp_path / "big.mp4"
    f.write_bytes(b"x" * (2 * 1024 * 1024))
    reason = oversize_reason(f, limit_mb=1)
    assert reason is not None
    assert "2" in reason and "1" in reason


def test_missing_file_is_reported_not_crashed(tmp_path):
    assert oversize_reason(tmp_path / "nope.mp4", limit_mb=10) is not None
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_sending.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.services.sending'`.

- [ ] **Step 3: Создать `bot/services/sending.py`**

```python
from __future__ import annotations

import asyncio
import re
from enum import Enum
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiogram.exceptions import (
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
)

# Жёстко зашито в telegram-bot-api (HttpServer.h), флага для изменения нет.
SERVER_IDLE_TIMEOUT = 500
# Порог, после которого сетевую ошибку считаем «файл, вероятно, доехал».
# Ниже серверного лимита — с запасом на установление соединения и погрешность.
PROBABLY_DELIVERED_AFTER = 400.0

_TRANSPORT_ERRORS = (TelegramNetworkError, asyncio.TimeoutError, aiohttp.ClientError)
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SendVerdict(Enum):
    RETRY = "retry"
    GIVE_UP = "give_up"
    PROBABLY_DELIVERED = "probably_delivered"
    TOO_LARGE = "too_large"


def classify_send_failure(
    exc: BaseException, elapsed: float, attempt: int, max_attempts: int
) -> SendVerdict:
    """Что делать со сбоем отправки.

    Порядок проверок важен: `TelegramEntityTooLarge` наследует
    `TelegramNetworkError`, и без явной проверки первым оверсайз-файл уходил
    в сетевую ветку и переотправлялся до победного.
    """
    if isinstance(exc, TelegramEntityTooLarge):
        return SendVerdict.TOO_LARGE

    if isinstance(exc, TelegramRetryAfter):
        return SendVerdict.RETRY if attempt < max_attempts - 1 else SendVerdict.GIVE_UP

    if isinstance(exc, _TRANSPORT_ERRORS):
        # Долгая отправка, оборвавшаяся у серверного IDLE_TIMEOUT: файл
        # доставлен, потерян только ответ. Повтор создал бы дубль в чате.
        if elapsed >= PROBABLY_DELIVERED_AFTER:
            return SendVerdict.PROBABLY_DELIVERED
        return SendVerdict.RETRY if attempt < max_attempts - 1 else SendVerdict.GIVE_UP

    return SendVerdict.GIVE_UP


def sanitize_filename(name: str) -> str:
    """Приводит имя к `[A-Za-z0-9._-]`.

    Нужно из-за двойного URL-декодирования на стороне сервера: чем меньше
    экзотики в имени, тем меньше поводов для расхождений.
    """
    cleaned = _UNSAFE_NAME_RE.sub("_", name).strip("._-")
    return cleaned or "file"


def to_file_uri(path: Path) -> str:
    """Строит `file:///…` для локальной отдачи.

    Кодирование двойное: сервер декодирует аргумент формы сначала на HTTP-слое,
    затем ещё раз в `get_local_file_path`.
    """
    once = quote(str(path), safe="/")
    twice = quote(once, safe="/")
    return f"file://{twice}"


def oversize_reason(path: Path, limit_mb: int) -> str | None:
    """Сообщение о превышении лимита либо None, если файл можно отправлять."""
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        return "Файл не найден на диске — попробуй ещё раз."
    if size_mb > limit_mb:
        return (
            f"Файл весит {size_mb:.0f} МБ, а предел отправки — {limit_mb} МБ. "
            "Попробуй ссылку на более короткое видео."
        )
    return None
```

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_sending.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/services/sending.py tests/test_sending.py
git commit -m "feat(sending): size gate, file:// URIs and send-failure classification"
```

---

### Task 10: Структурная гарантия уборки — контекст рабочей папки

Сегодня `finally` накрывает только отправку, а провал загрузки уходит через `return`, стоящий **до** `try`, то есть мимо уборки. Делаем гарантию структурной и тестируемой.

**Files:**
- Modify: `bot/services/cleanup.py` (добавить `work_dir`)
- Test: `tests/test_work_dir.py`

**Interfaces:**
- Produces: `async work_dir(root: Path) -> AsyncIterator[Path]` — асинхронный контекстный менеджер: создаёт уникальную папку, помечает её активной, удаляет при выходе **на любом пути**.
- Потребители: Задача 11.

- [ ] **Step 1: Написать падающий тест `tests/test_work_dir.py`**

```python
import asyncio
from pathlib import Path

import pytest

from bot.services.cleanup import active_dirs, work_dir


async def test_creates_and_removes_directory(tmp_path):
    async with work_dir(tmp_path) as d:
        assert d.exists() and d.is_dir()
        (d / "video.mp4").write_bytes(b"x" * 100)
        captured = d
    assert not captured.exists()


async def test_removes_directory_on_exception(tmp_path):
    captured = None
    with pytest.raises(RuntimeError):
        async with work_dir(tmp_path) as d:
            captured = d
            (d / "video.mp4").write_bytes(b"x")
            raise RuntimeError("boom")
    assert not captured.exists()


async def test_removes_directory_on_cancellation(tmp_path):
    # Регрессия: guard в загрузчике ловил Exception и пропускал CancelledError,
    # поэтому при остановке бота частично скачанные файлы оставались навсегда.
    captured = {}

    async def job():
        async with work_dir(tmp_path) as d:
            captured["dir"] = d
            (d / "part.mp4").write_bytes(b"x")
            await asyncio.sleep(30)

    task = asyncio.create_task(job())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not captured["dir"].exists()


async def test_directory_is_protected_while_active(tmp_path):
    async with work_dir(tmp_path) as d:
        assert d in active_dirs
    assert d not in active_dirs


async def test_directories_are_unique(tmp_path):
    async with work_dir(tmp_path) as a, work_dir(tmp_path) as b:
        assert a != b


async def test_survives_already_removed_directory(tmp_path):
    import shutil

    async with work_dir(tmp_path) as d:
        shutil.rmtree(d)
    # выход не должен бросить
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_work_dir.py -v`
Expected: FAIL — `ImportError: cannot import name 'work_dir'`.

- [ ] **Step 3: Добавить `work_dir` в `bot/services/cleanup.py`**

Дописать импорты `from collections.abc import AsyncIterator` и `from uuid import uuid4`, затем добавить:

```python
@contextlib.asynccontextmanager
async def work_dir(root: Path) -> AsyncIterator[Path]:
    """Рабочая папка одной загрузки, гарантированно удаляемая при выходе.

    Кто создал — тот и удаляет: это единственный способ не зависеть от того,
    по какой из веток (успех, провал, исключение, отмена) ушёл хендлер.
    """
    path = Path(root) / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        # reserve_dir защищает папку от подметальщика на всё время работы.
        with reserve_dir(path):
            yield path
    finally:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception as exc:
            logger.error("Failed to remove work dir {}: {}", path, exc)
```

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_work_dir.py -v`
Expected: PASS, 6 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/services/cleanup.py tests/test_work_dir.py
git commit -m "feat(cleanup): async work_dir context manager guaranteeing removal on every path"
```

---

### Task 11: Перестройка хендлера — прогресс, `file://`, единый `finally`

Самая крупная задача. Собирает вместе всё предыдущее.

**Files:**
- Modify: `bot/handlers/user.py:36` (семафор), `:60-64` (`_safe_edit`), `:67-95` (`_send_with_retry`), `:256-423` (тело `handle_url`)
- Test: `tests/test_user_helpers.py`

**Interfaces:**
- Consumes: `work_dir`, `ProgressReporter`, `render_download_status`, `render_upload_status`, `RateTracker`, `classify_send_failure`, `SendVerdict`, `oversize_reason`, `to_file_uri`, `probe_media`, `video_reject_reason`, `download_media(url, platform, work_dir, progress_cb)`.
- Produces:
  - `upload_rates: RateTracker` — общий на процесс оценщик скорости отдачи.
  - `ProbablyDelivered` — исключение: отправка оборвалась у серверного `IDLE_TIMEOUT`, но файл, скорее всего, доехал.
  - `SendRejected` — исключение: файл забракован гейтом размера или ffprobe; текст предназначен пользователю.
  - `SendTally` — счётчик `sent`/`total` для честного «отправлено N из M».
  - `_send_single(message, reporter, dl_result, platform, tally)` и `_send_album(...)` — те же параметры.

- [ ] **Step 1: Написать падающий тест `tests/test_user_helpers.py`**

```python
import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge, TelegramNetworkError
from aiogram.methods import SendMessage

from bot.handlers.user import _send_with_retry

M = SendMessage(chat_id=1, text="x")


async def test_returns_result_on_success():
    async def send():
        return "sent"

    assert await _send_with_retry(send) == "sent"


async def test_oversized_file_is_not_retried():
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        raise TelegramEntityTooLarge(method=M, message="Request Entity Too Large")

    with pytest.raises(TelegramEntityTooLarge):
        await _send_with_retry(send)
    assert calls == 1, "оверсайз-файл переотправлялся — это до часа зависания на 1.5 ГБ"


async def test_quick_network_error_is_retried(monkeypatch):
    monkeypatch.setattr("bot.handlers.user.SEND_RETRY_DELAYS", (0, 0, 0))
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TelegramNetworkError(method=M, message="reset")
        return "ok"

    assert await _send_with_retry(send) == "ok"
    assert calls == 3


async def test_long_failure_raises_probably_delivered(monkeypatch):
    from bot.handlers.user import ProbablyDelivered

    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        # Имитируем отправку, оборвавшуюся у серверного IDLE_TIMEOUT.
        raise TelegramNetworkError(method=M, message="Server disconnected")

    # classify_send_failure читает константу из своего модуля, поэтому
    # патчим её именно там.
    monkeypatch.setattr("bot.services.sending.PROBABLY_DELIVERED_AFTER", 0.0)

    with pytest.raises(ProbablyDelivered):
        await _send_with_retry(send)
    assert calls == 1, "повтор такой отправки создал бы дубль в чате"


async def test_bad_request_is_not_retried():
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        raise TelegramBadRequest(method=M, message="Bad Request: chat not found")

    with pytest.raises(TelegramBadRequest):
        await _send_with_retry(send)
    assert calls == 1
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_user_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name 'ProbablyDelivered'`.

- [ ] **Step 3: Переписать `_send_with_retry` в `bot/handlers/user.py`**

Добавить импорты:

```python
import time

from bot.services.cleanup import work_dir
from bot.services.media_probe import probe_media, video_reject_reason
from bot.services.progress import (
    ProgressReporter,
    RateTracker,
    render_download_status,
    render_upload_status,
)
from bot.services.sending import (
    PROBABLY_DELIVERED_AFTER,
    SendVerdict,
    classify_send_failure,
    oversize_reason,
    to_file_uri,
)
```

Рядом с `download_semaphore` заменить его и добавить оценщик:

```python
# Накрывает и загрузку, и отдачу: канал один, и параллельные аплоады делят
# его между собой, приближая каждый к серверному лимиту в 500 секунд.
download_semaphore = asyncio.Semaphore(2)
# Оценка остатка отдачи строится на замерах прошлых отправок, а не на константе.
upload_rates = RateTracker()


class ProbablyDelivered(Exception):
    """Отправка оборвалась у серверного IDLE_TIMEOUT, но файл, скорее всего, доехал."""
```

Заменить `_send_with_retry` (строки 67-95) на:

```python
async def _send_with_retry(send_coro_factory):
    """Отправляет с повторами, но только там, где повтор безопасен.

    Оверсайз не повторяем никогда, а долгий обрыв трактуем как доставку:
    telegram-bot-api закрывает соединение по IDLE_TIMEOUT=500 с уже после того,
    как файл ушёл в Telegram, и повтор дал бы дубль в чате.
    """
    max_attempts = len(SEND_RETRY_DELAYS) + 1
    for attempt in range(max_attempts):
        started = time.monotonic()
        try:
            return await send_coro_factory()
        except Exception as exc:
            elapsed = time.monotonic() - started
            verdict = classify_send_failure(exc, elapsed, attempt, max_attempts)

            if verdict is SendVerdict.PROBABLY_DELIVERED:
                logger.warning(
                    "Send failed after {:.0f}s, treating as delivered | {}", elapsed, exc
                )
                raise ProbablyDelivered() from exc
            if verdict is not SendVerdict.RETRY:
                raise

            delay = getattr(exc, "retry_after", None) or SEND_RETRY_DELAYS[attempt]
            logger.warning("Send retry {}/{} in {}s | {}", attempt + 1, max_attempts, delay, exc)
            await asyncio.sleep(delay)
```

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_user_helpers.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 5: Добавить помощники отправки**

Отправка выносится в две функции, чтобы `handle_url` осталась читаемой, а гейты
могли отказывать изнутри исключением, а не флагом. Добавить в `bot/handlers/user.py`
рядом с `_send_with_retry`:

```python
@dataclass
class SendTally:
    """Счётчик доставленного. Нужен, чтобы при частичном сбое альбома
    сообщить пользователю честное «отправлено N из M», а не «0 из M»."""
    sent: int = 0
    total: int = 1


class SendRejected(Exception):
    """Файл забракован до отправки (размер или ffprobe-гейт). Текст — для пользователя."""


async def _send_single(message, reporter, dl_result, platform, tally: SendTally) -> None:
    path = Path(dl_result.file_path)
    source = platform.capitalize()

    reason = oversize_reason(path, settings.MAX_FILE_SIZE_MB)
    if reason:
        raise SendRejected(reason)

    if dl_result.media_type == "image":
        # sendPhoto ограничен 10 МБ даже на локальном сервере, поэтому
        # крупные картинки уходят документом.
        if (dl_result.file_size_mb or 0) > 10:
            await _send_with_retry(
                lambda: message.reply_document(
                    document=to_file_uri(path), caption=f"✅ Изображение из {source}"
                )
            )
        else:
            await _send_with_retry(
                lambda: message.reply_photo(
                    photo=to_file_uri(path), caption=f"✅ Изображение из {source}"
                )
            )
        tally.sent = 1
        return

    info = await probe_media(path)
    reject = video_reject_reason(info)
    if reject:
        # Ловит video-only DASH-фрагмент и обрезанный файл ДО того, как мы
        # потратим минуты на его отдачу.
        logger.error("ffprobe gate rejected file | {} | {}", path, reject)
        raise SendRejected(f"{reject}. Попробуй ещё раз.")

    size_bytes = path.stat().st_size
    await reporter.force(
        render_upload_status(size_bytes, 0.0, upload_rates.estimate_seconds(size_bytes))
    )
    started = time.monotonic()
    await _send_with_retry(
        lambda: message.reply_video(
            video=to_file_uri(path),
            caption=f"✅ Видео из {source}",
            width=info.width,
            height=info.height,
            duration=int(info.duration),
            supports_streaming=True,
        )
    )
    # Замер идёт в общий оценщик: следующая отдача покажет осмысленный остаток.
    upload_rates.record(size_bytes, time.monotonic() - started)
    tally.sent = 1


async def _send_album(message, reporter, dl_result, platform, tally: SendTally) -> None:
    caption = f"✅ Медиа из {platform.capitalize()}"
    paths = dl_result.file_paths
    total = len(paths)
    first_media_captioned = False

    for chunk_start in range(0, total, MEDIA_GROUP_CHUNK_SIZE):
        chunk = paths[chunk_start:chunk_start + MEDIA_GROUP_CHUNK_SIZE]
        sendable: list[tuple[Path, bool]] = []
        oversized_images: list[Path] = []

        for path_str in chunk:
            path = Path(path_str)
            if oversize_reason(path, settings.MAX_FILE_SIZE_MB):
                # Негодный элемент пропускаем, а не роняем весь альбом.
                logger.warning("Skipping oversized album item: {}", path)
                continue
            is_image = path.suffix.lower() in IMAGE_EXTS
            try:
                size_mb = path.stat().st_size / (1024 * 1024)
            except OSError:
                continue
            # Telegram отклоняет фото > 10 МБ и не допускает смешивания
            # документов с фото/видео в одной media group.
            if is_image and size_mb > 10:
                oversized_images.append(path)
                continue
            sendable.append((path, is_image))

        await reporter.set(f"📤 <b>Отправляю…</b>\nФайл {tally.sent + 1} из {total}")

        if len(sendable) == 1:
            path, is_image = sendable[0]
            cap = None if first_media_captioned else caption
            first_media_captioned = True
            if is_image:
                await _send_with_retry(
                    lambda p=path, c=cap: message.reply_photo(photo=to_file_uri(p), caption=c)
                )
            else:
                await _send_with_retry(
                    lambda p=path, c=cap: message.reply_video(video=to_file_uri(p), caption=c)
                )
            tally.sent += 1
        elif len(sendable) > 1:
            media_group = []
            for path, is_image in sendable:
                cap = None if first_media_captioned else caption
                first_media_captioned = True
                uri = to_file_uri(path)
                media_group.append(
                    InputMediaPhoto(media=uri, caption=cap)
                    if is_image
                    else InputMediaVideo(media=uri, caption=cap)
                )
            await _send_with_retry(lambda mg=media_group: message.reply_media_group(media=mg))
            tally.sent += len(media_group)

        for path in oversized_images:
            await _send_with_retry(lambda p=path: message.reply_document(document=to_file_uri(p)))
            tally.sent += 1

    if tally.sent == 0:
        raise SendRejected("Ни один файл не прошёл проверку перед отправкой.")
```

Добавить в импорты модуля:

```python
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from bot.services.downloader import IMAGE_EXTS, download_media
```

- [ ] **Step 6: Переписать тело `handle_url` от статус-сообщения до конца**

Заменить всё от строки 256 (создание `status_msg`) до конца функции на:

```python
    status_msg = await message.reply("🔎 <b>Анализирую ссылку…</b>")
    reporter = ProgressReporter(status_msg)

    # work_dir создаёт папку и удаляет её при выходе ЛЮБЫМ путём — успех,
    # ошибка, исключение, отмена. Раньше провал загрузки уходил через return,
    # стоявший до try, то есть мимо уборки.
    async with work_dir(Path(settings.DOWNLOAD_ROOT)) as wd:
        try:
            free_gb = shutil.disk_usage(settings.DOWNLOAD_ROOT).free / (1024 ** 3)
        except OSError as exc:
            logger.error("Cannot stat download root: {}", exc)
            free_gb = 0.0
        if free_gb < settings.MIN_FREE_DISK_GB:
            logger.error("Not enough free disk: {:.1f} GB", free_gb)
            await reporter.force("⚠️ На сервере закончилось место. Попробуй позже.")
            return

        # Семафор охватывает и загрузку, и отдачу: канал один, и параллельные
        # аплоады приближают каждый к серверному лимиту в 500 секунд.
        if download_semaphore.locked():
            await reporter.force("⏳ <b>В очереди…</b>\nСейчас идут другие загрузки")

        async with download_semaphore:
            async def _progress(p) -> None:
                await reporter.set(render_download_status(p))

            await reporter.force("⬇️ <b>Скачиваю…</b>")
            dl_result = await download_media(url, platform, wd, _progress)

            if not dl_result.success:
                async with async_session() as session, session.begin():
                    await log_download(session, db_user_id, url, platform, "failed")
                await reporter.force(
                    f"❌ <b>Не удалось скачать</b>\n{dl_result.error_message}"
                )
                return

            is_album = bool(dl_result.file_paths and len(dl_result.file_paths) > 1)
            tally = SendTally(
                total=len(dl_result.file_paths) if dl_result.file_paths else 1
            )

            try:
                if is_album:
                    await _send_album(message, reporter, dl_result, platform, tally)
                else:
                    await _send_single(message, reporter, dl_result, platform, tally)

                async with async_session() as session, session.begin():
                    if not has_subscription:
                        await decrement_free_downloads(session, db_user_id)
                    await increment_total_downloads(session, db_user_id)
                    await log_download(
                        session, db_user_id, url, platform, "success", dl_result.file_size_mb
                    )

                try:
                    await status_msg.delete()
                except Exception:
                    pass

                try:
                    await message.answer(
                        "✅ Готово! Что дальше?",
                        reply_markup=get_after_download_kb(
                            is_admin=_is_admin(message.from_user.id)
                        ),
                    )
                except Exception:
                    pass

            except SendRejected as exc:
                async with async_session() as session, session.begin():
                    await log_download(session, db_user_id, url, platform, "failed")
                await reporter.force(f"❌ <b>Не могу отправить</b>\n{exc}")

            except ProbablyDelivered:
                # Отправка оборвалась у серверного IDLE_TIMEOUT=500 с. Файл при
                # этом доезжает, теряется только ответ, поэтому считаем успехом
                # и НЕ повторяем — повтор дал бы дубль в чате.
                async with async_session() as session, session.begin():
                    if not has_subscription:
                        await decrement_free_downloads(session, db_user_id)
                    await increment_total_downloads(session, db_user_id)
                    await log_download(
                        session, db_user_id, url, platform, "success", dl_result.file_size_mb
                    )
                await reporter.force(
                    "📤 <b>Файл отправлен</b>\n"
                    "Telegram не подтвердил доставку вовремя — проверь чат. "
                    "Повторно отправлять не буду, чтобы не задвоить."
                )

            except (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError,
                    aiohttp.ClientError) as exc:
                logger.error(
                    "Network error while sending | sent={}/{} error={}",
                    tally.sent, tally.total, exc,
                )
                async with async_session() as session, session.begin():
                    await log_download(session, db_user_id, url, platform, "failed")
                if tally.total > 1:
                    await reporter.force(
                        f"⚠️ Отправлено {tally.sent} из {tally.total} файлов, "
                        "дальше произошла ошибка сети. Попробуй ещё раз."
                    )
                else:
                    await reporter.force(
                        "⚠️ Ошибка сети при отправке файла. Попробуй ещё раз."
                    )

            except Exception as exc:
                logger.exception("Failed to send file: {}", exc)
                async with async_session() as session, session.begin():
                    await log_download(session, db_user_id, url, platform, "failed")
                await reporter.force("⚠️ Ошибка при отправке файла. Попробуй ещё раз.")
```

**Прежний блок `finally` с ручным удалением файлов удалить целиком** — уборкой
теперь владеет `work_dir`. Импорт `remove_file` из `bot.services.cleanup` и импорт
`FSInputFile` из `aiogram.types` тоже становятся ненужными.

- [ ] **Step 7: Прогнать все тесты и проверить, что `FSInputFile` больше не используется**

```bash
./scripts/test.sh -v
grep -n "FSInputFile" bot/ -r || echo "FSInputFile больше не используется"
```
Expected: тесты зелёные, вывод `FSInputFile больше не используется`.

- [ ] **Step 8: Проверить, что между статусом и `work_dir` нет выходов**

```bash
sed -n '/status_msg = await message.reply/,/async with work_dir/p' bot/handlers/user.py | grep -n "return" && echo "ОШИБКА: есть return до work_dir" || echo "ок, выходов нет"
```
Expected: `ок, выходов нет`.

- [ ] **Step 9: Commit**

```bash
git add bot/handlers/user.py tests/test_user_helpers.py
git commit -m "feat(handler): progress reporting, file:// sends and structural cleanup guarantee"
```

---

### Task 12: Подключение бота к локальному серверу

**Files:**
- Modify: `bot/__main__.py:22-27`
- Test: `tests/test_session_wiring.py`

**Interfaces:**
- Consumes: `settings.TELEGRAM_API_BASE`, `settings.TELEGRAM_REQUEST_TIMEOUT`.
- Produces: `build_session() -> AiohttpSession` — вынесено отдельной функцией, чтобы поддавалось проверке без запуска бота.

- [ ] **Step 1: Написать падающий тест `tests/test_session_wiring.py`**

```python
from bot.__main__ import build_session
from bot.config import settings


def test_session_points_at_local_server():
    session = build_session()
    url = session.api.api_url(token="123:ABC", method="sendVideo")
    assert url.startswith(settings.TELEGRAM_API_BASE)
    assert url.endswith("/bot123:ABC/sendVideo")


def test_session_is_marked_local():
    # Влияет на то, как aiogram строит путь при скачивании файлов.
    assert build_session().api.is_local is True


def test_session_timeout_survives_long_uploads():
    session = build_session()
    assert session.timeout == settings.TELEGRAM_REQUEST_TIMEOUT
    # Должен быть больше серверного IDLE_TIMEOUT=500, иначе мы будем
    # обрывать соединение раньше сервера и терять диагностику.
    assert session.timeout > 500
```

- [ ] **Step 2: Прогнать тест, убедиться, что падает**

Run: `./scripts/test.sh tests/test_session_wiring.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_session'`.

- [ ] **Step 3: Изменить `bot/__main__.py`**

Добавить импорт:

```python
from aiogram.client.telegram import TelegramAPIServer
```

Добавить функцию перед `main()`:

```python
def build_session() -> AiohttpSession:
    """Сессия, направленная на самостоятельно поднятый telegram-bot-api.

    Он снимает потолок в 50 МБ и позволяет отдавать файл ссылкой file://
    вместо перекладывания гигабайтов через HTTP.
    """
    return AiohttpSession(
        api=TelegramAPIServer.from_base(settings.TELEGRAM_API_BASE, is_local=True),
        timeout=settings.TELEGRAM_REQUEST_TIMEOUT,
    )
```

В `main()` заменить `session = AiohttpSession(timeout=180)` на `session = build_session()`.

- [ ] **Step 4: Прогнать тесты**

Run: `./scripts/test.sh tests/test_session_wiring.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/__main__.py tests/test_session_wiring.py
git commit -m "feat(transport): point the bot at the self-hosted Bot API server"
```

---

### Task 13: Инфраструктура — контейнер сервера и общий том

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` из `.env`.
- Produces: работающий `telegram-bot-api` на `127.0.0.1:8081` и общая папка `/srv/jw_downloads`.

- [ ] **Step 1: Выяснить имена переменных окружения у образа**

Имена env-переменных задаёт entrypoint образа, и угадывать их нельзя.

```bash
docker pull aiogram/telegram-bot-api:latest
docker inspect --format '{{json .Config.Entrypoint}} {{json .Config.Cmd}}' aiogram/telegram-bot-api:latest
docker run --rm --entrypoint sh aiogram/telegram-bot-api:latest -c \
  'cat /entrypoint.sh 2>/dev/null || ls -la /'
```
Записать фактические имена. Если entrypoint прочитать не удалось — задавать флаги
напрямую через `command:`, а не через `environment:`. Проверить заодно наличие
`curl` или `wget` для healthcheck:
```bash
docker run --rm --entrypoint sh aiogram/telegram-bot-api:latest -c \
  'command -v curl; command -v wget; echo "---"'
```

- [ ] **Step 2: Создать каталоги на хосте**

```bash
sudo mkdir -p /mnt/storage/jw_downloads /mnt/storage/jw_tg_api/data /mnt/storage/jw_tg_api/temp
sudo chmod 0777 /mnt/storage/jw_downloads
df -h /mnt/storage
```
Expected: не менее 100 ГБ свободно.

- [ ] **Step 3: Переписать `docker-compose.yml`**

```yaml
services:
  telegram-bot-api:
    image: aiogram/telegram-bot-api:latest
    container_name: jw_telegram_bot_api
    restart: always
    # Та же причина, что и у бота: Docker-NAT на этом хосте роняет крупные
    # исходящие передачи (MTU 1280 на пути до Telegram).
    network_mode: host
    environment:
      TELEGRAM_API_ID: ${TELEGRAM_API_ID}
      TELEGRAM_API_HASH: ${TELEGRAM_API_HASH}
      # Локальный режим: лимит 2000 МБ вместо 50 и приём файлов по file://.
      TELEGRAM_LOCAL: 1
      TELEGRAM_HTTP_IP_ADDRESS: 127.0.0.1
      TELEGRAM_HTTP_PORT: 8081
      TELEGRAM_WORK_DIR: /var/lib/telegram-bot-api
      # По умолчанию temp-dir — это $TMPDIR, то есть eMMC, где всего 2.3 ГБ.
      TELEGRAM_TEMP_DIR: /tmp/telegram-bot-api
    volumes:
      # Каталог бота внутри тома называется ПОЛНЫМ ТОКЕНОМ бота — том является
      # секретным материалом и не должен попадать в бэкапы и репозиторий.
      - /mnt/storage/jw_tg_api/data:/var/lib/telegram-bot-api
      - /mnt/storage/jw_tg_api/temp:/tmp/telegram-bot-api
      # Файлы на отдачу. Путь ОБЯЗАН совпадать с путём внутри контейнера бота,
      # иначе переданный по file:// путь у сервера не разрешится.
      # Каталог лежит вне TELEGRAM_WORK_DIR: сборщик мусора TDLib ходит только
      # по своим подпапкам, и так наши файлы вне его досягаемости.
      - /mnt/storage/jw_downloads:/srv/jw_downloads:ro
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  bot:
    build:
      context: .
      target: base
    container_name: jw_downloader_bot
    restart: always
    depends_on:
      - telegram-bot-api
    # Бот работает только на исходящих соединениях (long polling, без входящих
    # портов). Docker-NAT на этом хосте роняет крупные аплоады в Telegram:
    # путь до Telegram имеет MTU 1280, а ICMP «fragmentation needed» не
    # доставляется обратно в контейнер через NAT — отправка файлов >~1.5 МБ
    # висит и отваливается по таймауту. Сеть хоста тот же аплоад отдаёт за ~4с,
    # поэтому используем host-сеть напрямую и обходим проблему NAT целиком.
    network_mode: host
    env_file: .env
    volumes:
      - bot_data:/app/data
      - ./secrets:/app/secrets:ro
      # Прежний tmpfs на 200 МБ убран: он жил в RAM, которой на этом хосте
      # всего 3.8 ГБ, и гигабайтный файл туда физически не помещается.
      - /mnt/storage/jw_downloads:/srv/jw_downloads
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  bot_data:
```

Порт `8082` (стат-порт) **не публиковать и не включать**: его тело содержит токен
бота открытым текстом, а `?v=<n>` меняет verbosity сервера.

- [ ] **Step 4: Добавить healthcheck сервера**

Использовать вариант под то, что нашлось на Шаге 1. Основной порт отдаёт `404`
на `/`, и это признак живости:

```yaml
    healthcheck:
      test: ["CMD-SHELL", "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/ | grep -q 404"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
```

Если `curl` в образе нет, а есть `wget` — заменить на
`wget -q -O /dev/null http://127.0.0.1:8081/ 2>&1 | grep -q . || true` и проверить
вручную; если нет ни того, ни другого — healthcheck не добавлять и отметить это
в README.

- [ ] **Step 5: Обновить `.gitignore`**

Дописать в конец:

```
# ── Локальный Bot API ──
# Рабочий каталог telegram-bot-api называет подпапку ПОЛНЫМ ТОКЕНОМ бота.
# Даже случайно попасть в репозиторий он не должен.
tg_api_data/
*telegram-bot-api*/
```

- [ ] **Step 6: Обновить `.dockerignore`**

Дописать:

```
tg_api_data/
docs/
```

- [ ] **Step 7: Обновить `README.md`**

Найти все упоминания старого лимита:

```bash
grep -n "50 МБ\|50MB\|50 MB\|MAX_FILE_SIZE_MB" README.md
```

Каждое исправить на актуальное значение. Затем добавить раздел «Локальный Bot API
сервер», описав: зачем он нужен (облачный Bot API даёт потолок 50 МБ), откуда
берутся `api_id`/`api_hash` (my.telegram.org, ключи приложения, не бота), почему
том `/mnt/storage/jw_tg_api/data` является секретным материалом (внутри него
подпапка называется полным токеном бота) и почему потолок равен 1500 МБ, а не
разрешённым API 2000 МБ (бюджет `IDLE_TIMEOUT = 500` с у сервера).

Проверить, что не осталось расхождений:

```bash
grep -n "50 МБ" README.md && echo "ОСТАЛИСЬ УПОМИНАНИЯ — исправить" || echo "чисто"
```

- [ ] **Step 8: Проверить конфигурацию**

```bash
docker compose config >/dev/null && echo "compose ок"
grep -c "8082" docker-compose.yml || echo "стат-порт не публикуется — верно"
```
Expected: `compose ок` и подтверждение отсутствия `8082`.

- [ ] **Step 9: Commit**

```bash
git add docker-compose.yml .gitignore .dockerignore README.md
git commit -m "feat(infra): add self-hosted telegram-bot-api service and shared download mount"
```

---

### Task 14: Миграция, смоук-тест и замер потолка

Единственная задача, где действия необратимы. Выполнять **только** после того, как все предыдущие зелёные.

**Files:**
- Modify: `bot/config.py` и `.env.example` (по результату замера, если он позволит)
- Create: `docs/superpowers/plans/2026-09-03-smoke-results.md`

- [ ] **Step 1: Убедиться, что все тесты зелёные**

Run: `./scripts/test.sh -v`
Expected: PASS, ни одного упавшего.

- [ ] **Step 2: Попросить владельца вписать ключи**

`TELEGRAM_API_ID` и `TELEGRAM_API_HASH` в `.env` — с https://my.telegram.org.
Это ключи **приложения**, не бота; их можно взять из соседнего проекта.
**Не читать и не логировать их значения.**

- [ ] **Step 3: Поднять сервер и проверить живость**

```bash
docker compose up -d telegram-bot-api
sleep 10
docker compose logs --tail=50 telegram-bot-api
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8081/
```
Expected: код `404` (сервер жив, метод не указан) и отсутствие ошибок авторизации в логах.

- [ ] **Step 4: Смоук-тест на тестовом токене**

Владелец создаёт отдельного бота через @BotFather. Запустить копию бота с этим
токеном (переопределив `BOT_TOKEN` в окружении), боевой бот в это время остаётся
на облаке.

Проверить по каждой платформе: YouTube, Instagram reels, Instagram карусель `/p/`,
TikTok видео, TikTok `/photo/`, Facebook, Pinterest.

Для каждой фиксировать: успех, размер, разрешение по `ffprobe`, наличие звука,
видимость прогресса, отсутствие водяного знака.

- [ ] **Step 5: Замерить фактическую скорость отдачи**

Взять видео около 1 ГБ и засечь время между началом отдачи и появлением файла в чате:

```bash
docker compose logs bot | grep -E "Отправляю|upload|Send" | tail -20
```

Посчитать МБ/с. **Это решающее число.**

- [ ] **Step 6: Зафиксировать итоговый `MAX_FILE_SIZE_MB`**

Правило: `MAX_FILE_SIZE_MB = замеренная_скорость_МБс × 400` (400 с вместо 500 —
запас на нестабильность канала), но не больше 1900.

| Замер | Ставить |
|---|---|
| ≥ 4.75 МБ/с | 1900 |
| 3.75–4.75 МБ/с | 1500 (оставить как есть) |
| 2.5–3.75 МБ/с | 1000 |
| < 2.5 МБ/с | 800 и отдельно разобраться, почему канал медленнее замеренных 9.6 МБ/с |

Изменить `bot/config.py` и комментарий в `.env.example`.

- [ ] **Step 7: Проверить, что диск чист после всех сценариев**

```bash
ls -la /mnt/storage/jw_downloads/
du -sh /mnt/storage/jw_downloads /mnt/storage/jw_tg_api
df -h / /mnt/storage
```
Expected: `/mnt/storage/jw_downloads` пуст. Если нет — не двигаться дальше,
а найти путь, который оставил файлы.

- [ ] **Step 8: Отдельно проверить провальные сценарии**

- битая ссылка → статус меняется на ошибку, папка удалена;
- перезапуск контейнера в середине загрузки → папка удалена или подметена;
- файл больше лимита → отказ без ретраев, папка удалена.

```bash
ls -la /mnt/storage/jw_downloads/
```
Expected: пусто после каждого.

- [ ] **Step 9: Записать результаты**

Создать `docs/superpowers/plans/2026-09-03-smoke-results.md` с таблицей по
платформам, замеренной скоростью и итоговым `MAX_FILE_SIZE_MB`.

- [ ] **Step 10: Сверить код в контейнере с git HEAD**

```bash
git rev-parse HEAD
for f in bot/services/downloader.py bot/handlers/user.py bot/services/cleanup.py \
         bot/services/progress.py bot/__main__.py; do
  echo -n "$f  "
  docker compose exec -T bot sha256sum "/app/$f" | cut -d' ' -f1
  sha256sum "$f" | cut -d' ' -f1
done
```
Expected: пары хешей совпадают.

- [ ] **Step 11: НЕОБРАТИМЫЙ ШАГ — перевод боевого бота**

Выполнять только с явного подтверждения владельца в этот момент.

```bash
# Разлогинить боевого бота с ОБЛАЧНОГО Bot API. После этого облачный API
# перестаёт обслуживать этот токен, и бот работает только через свой сервер.
curl -s "https://api.telegram.org/bot<BOEVOY_TOKEN>/logOut"
```
Expected: `{"ok":true,"result":true}`.

Затем `docker compose up -d --build` с боевым токеном и проверка одной ссылкой.

Возврат на облако возможен только повторным `logOut` уже с локального сервера,
и он **удалит каталог бота на нём** (`Client.cpp` делает `rmrf`).

- [ ] **Step 12: Финальный коммит**

```bash
git add bot/config.py .env.example docs/superpowers/plans/2026-09-03-smoke-results.md
git commit -m "chore: record smoke test results and finalize the size cap"
```

---

## Приложение: сверка плана со спекой

| Требование спеки | Задача |
|---|---|
| §4.1 контейнер сервера, `--local` | 13 |
| §4.1 сессия aiogram на локальный сервер | 12 |
| §4.1 отдача по `file://`, общий bind-mount | 9, 11, 13 |
| §4.1 санитизация имён | 9 |
| §4.2 потолок из бюджета 500 с | 2, 14 |
| §4.3 `TelegramEntityTooLarge` без ретраев | 9, 11 |
| §4.3 гейт размера до отправки | 9, 11 |
| §4.3 «вероятно доставлено» | 9, 11 |
| §4.4 `ProgressReporter`, троттлинг, no-op на «not modified» | 4 |
| §4.4 потоковое чтение stdout, сток stderr | 6 |
| §4.4 `--newline` и `--progress-template` | 4, 7 |
| §4.4 `progress_cb` через `download_media` | 6, 11 |
| §4.4 четыре фазы статуса | 11 |
| §4.4 оценка отдачи по скользящей средней | 4, 11 |
| §4.5 папка на загрузку, владелец — хендлер | 10, 11 |
| §4.5 один `finally` на обе фазы | 10, 11 |
| §4.5 подметальщик: рекурсия, `stat()` в `try`, живучесть | 8 |
| §4.5 реестр активных папок | 8, 10 |
| §4.5 надзор за фоновой задачей | 8 |
| §4.5 предполётная проверка места | 11 |
| §4.6 ffprobe-гейт | 3, 11 |
| §4.6 белый список расширений | 5 |
| §4.6 снятие пина YouTube | 7 |
| §4.6 селекторы без арифметики, H.264 у Facebook | 7 |
| §4.6 убийство группы процессов | 6 |
| §4.7 семафор на обе фазы, «В очереди» | 11 |
| §5 новые настройки | 2 |
| §6 compose, тома, `.gitignore` | 13 |
| §7 раннбук миграции | 14 |
| §8 тест-план | 14 |
