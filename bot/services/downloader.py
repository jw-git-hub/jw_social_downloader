from __future__ import annotations

import asyncio
import contextlib
import html
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from loguru import logger

from bot.config import settings

DOWNLOAD_DIR = Path("/tmp/jw_downloads")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
GALLERY_DL_FALLBACK_PLATFORMS = {"instagram", "pinterest", "tiktok"}
# TikTok периодически отдаёт транзиторный WAF-челлендж (JS rehydration / HTTP 403):
# видео доступно, но конкретная попытка срывается. Делаем один мягкий ретрай.
TIKTOK_MAX_ATTEMPTS = 2
TIKTOK_RETRY_DELAY = 5
TIKTOK_TRANSIENT_MARKERS = ("rehydration", "403", "unable to extract", "unable to download webpage")


@dataclass
class DownloadResult:
    file_path: str | None = None
    file_paths: list[str] | None = None
    file_size_mb: float | None = None
    success: bool = False
    error_message: str | None = None
    media_type: str | None = None


# ── Классификация ошибок загрузки ────────────────────────────────────────

# Классифицируем только те строки, которые сами утилиты пометили как ошибку:
# yt-dlp печатает «ERROR: ...», gallery-dl — «[extractor][error] ...».
# Прогресс, предупреждения и эхо аргументов в классификацию не попадают —
# именно из-за них раньше любой сбой объявлялся протуханием cookies.
_ERROR_LINE_RE = re.compile(
    r"(?:^|\s)(?:ERROR:|\[[^\]\s]+\]\[error\]|error:)",
    re.IGNORECASE,
)

# Порядок значим: специфичные правила стоят раньше общих. Например
# «rate-limit reached» — это фирменная фраза Instagram про мёртвую сессию,
# а не про троттлинг, поэтому dead_session идёт раньше rate_limit.
_ERROR_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "bot_check",
        re.compile(
            r"confirm you(?:'|’)?re not a bot|confirm you are not a bot|are you a robot",
            re.IGNORECASE,
        ),
        "🤖 Платформа просит подтвердить, что запрос не от робота. Попробуй позже.",
    ),
    (
        "age_gate",
        re.compile(
            r"age[- ]restricted|age[_ ]gate|confirm your age|age[_ ]verification"
            r"|inappropriate for some users",
            re.IGNORECASE,
        ),
        "🔞 Видео с возрастным ограничением.",
    ),
    (
        "dead_session",
        re.compile(
            r"http redirect to login page|login required|rate-limit reached"
            r"|cookies are no longer valid|the provided cookies"
            r"|session (?:has )?expired|not logged[ -]?in|please log ?in",
            re.IGNORECASE,
        ),
        "🍪 Платформа не пускает без авторизации: сессия истекла. Обратитесь к админу.",
    ),
    (
        "private",
        re.compile(
            r"\bprivate (?:video|post|account|profile|content)\b"
            r"|(?:video|post|account|profile) is private",
            re.IGNORECASE,
        ),
        "🔒 Это приватная публикация. Скачивание невозможно.",
    ),
    (
        "not_found",
        re.compile(
            r"http error 404\b|\b404:? not found\b|video unavailable"
            r"|no longer available|has been removed|does not exist"
            r"|(?:post|page|content) (?:isn'?t|is not) available",
            re.IGNORECASE,
        ),
        "🔍 Публикация не найдена. Возможно, она удалена или ссылка неверная.",
    ),
    (
        "geo_block",
        re.compile(
            r"geo[- ]?(?:restrict|block)|not available (?:in|from) your (?:country|location|region)"
            r"|blocked in your country",
            re.IGNORECASE,
        ),
        "🌍 Видео недоступно в текущем регионе.",
    ),
    (
        "rate_limit",
        re.compile(r"http error 429\b|\btoo many requests\b|\brate[- ]limit", re.IGNORECASE),
        "⏳ Слишком много запросов. Попробуй через минуту.",
    ),
    (
        "auth_required",
        re.compile(
            r"http error 40[13]\b|authentication required|requires (?:a )?(?:login|account|subscription)",
            re.IGNORECASE,
        ),
        "🔐 {platform}: требуется авторизация.",
    ),
    (
        "no_formats",
        re.compile(
            r"requested format (?:is )?not available|no video formats found"
            r"|no (?:suitable )?formats found|unsupported url",
            re.IGNORECASE,
        ),
        "🔄 Формат видео не поддерживается. Попробуй другую ссылку.",
    ),
    (
        "too_large",
        re.compile(r"max-?filesize|file is larger than", re.IGNORECASE),
        "📦 Файл слишком большой (больше {max_mb} МБ).",
    ),
)

# В фолбэке текст gallery-dl предпочтительнее: он на порядок человекочитаемее
# внутренних трейсбеков yt-dlp.
_SOURCE_PRIORITY = ("gallery-dl", "yt-dlp")


def _error_surface(text: str) -> str:
    """Оставляет из вывода утилиты только строки, помеченные как ошибка."""
    lines = [ln.strip() for ln in text.splitlines() if _ERROR_LINE_RE.search(ln)]
    return "\n".join(lines)


def _pick_fallback(outputs: Sequence[tuple[str, str]]) -> str:
    for wanted in _SOURCE_PRIORITY:
        for name, text in outputs:
            if name != wanted:
                continue
            candidate = _error_surface(text) or text.strip()
            if candidate:
                return candidate
    for _name, text in outputs:
        if text.strip():
            return text.strip()
    return "утилита завершилась без сообщения об ошибке"


def _parse_error(outputs: Sequence[tuple[str, str]], platform: str) -> str:
    """Человеческое сообщение об ошибке по выводам всех запущенных утилит.

    `outputs` — пары («yt-dlp» | «gallery-dl», stderr) в порядке запуска.
    """
    haystack = "\n".join(s for s in (_error_surface(t) for _n, t in outputs) if s)
    logger.debug("Parsing error | platform={} surface={}", platform, haystack[:500])

    for _name, pattern, template in _ERROR_RULES:
        if pattern.search(haystack):
            return template.format(
                platform=platform.capitalize(),
                max_mb=settings.MAX_FILE_SIZE_MB,
            )

    short_err = _pick_fallback(outputs)[:200]
    # Экранируем: сообщение уходит с parse_mode=HTML, а сырой вывод утилит
    # может содержать <, >, & и ломать разметку.
    return f"❌ Ошибка загрузки:\n<code>{html.escape(short_err)}</code>"


@contextlib.contextmanager
def _ephemeral_cookies() -> Iterator[Path | None]:
    # yt-dlp и gallery-dl по завершении перезаписывают файл, переданный в --cookies,
    # свежей cookie-jar. Если платформа в этот момент отдаёт транзиторный
    # неавторизованный ответ (разлогин), они пишут обрезанную банку без sessionid
    # поверх мастер-файла — и рабочие креды теряются безвозвратно. Поэтому утилитам
    # всегда подсовываем одноразовую копию во временной директории: что бы они в неё
    # ни записали, мастер-файл (secrets/cookies.txt) не трогаем.
    cookies_file = Path(settings.COOKIES_FILE) if settings.COOKIES_FILE else None
    if not cookies_file or not cookies_file.exists():
        yield None
        return

    tmp_dir = Path(tempfile.mkdtemp(prefix="jw_cookies_"))
    try:
        tmp_cookies = tmp_dir / "cookies.txt"
        shutil.copyfile(cookies_file, tmp_cookies)
        yield tmp_cookies
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_command(url: str, platform: str, output_path: Path, cookies_path: Path | None) -> list[str]:
    cmd = [
        "yt-dlp",
        "--no-check-certificates",
        "--socket-timeout", "30",
        "--retries", "3",
        "--age-limit", "99",
        "--max-filesize", f"{settings.MAX_FILE_SIZE_MB}M",
        "-o", str(output_path),
    ]

    if cookies_path is not None:
        cmd.extend(["--cookies", str(cookies_path)])
        logger.debug("Using cookies file: {}", cookies_path)

    ua = "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    accept_lang = "Accept-Language:en-US,en;q=0.9"

    if platform == "instagram":
        cmd.extend([
            # bv*+ba/b: Instagram прячет более высокое разрешение в отдельных
            # DASH-дорожках (video-only + audio), которые `best` пропускает.
            "-f", "bv*+ba/b",
            "--ignore-no-formats-error",
            "--add-header", ua,
            "--add-header", accept_lang,
        ])
    elif platform == "tiktok":
        # У текущего yt-dlp формат-ID TikTok — h264_<res>_* / bytevc1_<res>_* (склеенные
        # video+audio), плюс watermark-версии download / download_addr. Старых play_addr_*
        # больше нет. Порядок предпочтения: чистый H.264 (vcodec h264/avc1 — играется
        # плеером Telegram везде) → чистый любой видеокодек (bytevc1 = H.265) → любой
        # ВИДЕО-формат, включая watermark (лишь бы с картинкой). Ни одна ветка не берёт
        # audio-only: если TikTok спрятал видео и отдал только аудио, формат не найдётся,
        # и мы уйдём в gallery-dl fallback / внятную ошибку, а не пришлём «звук без картинки».
        cmd.extend([
            "--no-playlist",
            "--extractor-retries", "3",
            "-f", (
                "b[vcodec~='^(avc1|h264)'][format_id!=download][format_id!=download_addr]/"
                "b[vcodec!=none][format_id!=download][format_id!=download_addr]/"
                "b[vcodec!=none]"
            ),
            "--add-header", ua,
        ])
        cmd.extend(["--merge-output-format", "mp4"])
        # Прокси для TikTok (обход анти-бота дата-центрового IP). Активен только если
        # TIKTOK_PROXY задан в .env; пусто = прямое подключение, поведение как раньше.
        if settings.TIKTOK_PROXY:
            cmd.extend(["--proxy", settings.TIKTOK_PROXY])
            logger.debug("Using TikTok proxy")
    elif platform == "pinterest":
        # Pinterest в основном отдаёт изображения/доски, которые yt-dlp вообще не
        # тянет — приоритетно качаем через gallery-dl (см. download_media). Сюда
        # попадаем только как fallback для видео-пина, поэтому берём простой best.
        cmd.extend([
            "--no-playlist",
            "-f", "best",
            "--add-header", ua,
        ])
        cmd.extend(["--merge-output-format", "mp4"])
    elif platform == "facebook":
        # Facebook часто отдаёт видео-дорожки только в кодеке AV1 (av01), который
        # не проигрывается плеером Telegram и многими устройствами — получается
        # «звук без картинки». Прогрессивные форматы hd/sd — это H.264 + AAC,
        # совместимые везде, поэтому предпочитаем именно avc1. Сначала идут
        # size-aware DASH-ветки: если склейка avc1 укладывается в лимит — берём её;
        # иначе деградируем к hd, а затем к sd, чтобы слишком тяжёлый hd не ронял
        # загрузку по --max-filesize. AV1 не берём никогда — только avc1/mp4.
        cmd.extend([
            "--no-playlist",
            "-f", (
                f"bv*[vcodec^=avc1][filesize<{settings.MAX_FILE_SIZE_MB - 5}M]+ba[ext=m4a][filesize<5M]/"
                f"b[vcodec^=avc1][filesize<{settings.MAX_FILE_SIZE_MB}M]/"
                f"hd/sd/bv*[vcodec^=avc1]+ba[ext=m4a]/b[vcodec^=avc1]/b[ext=mp4]/b"
            ),
            "--add-header", ua,
            "--add-header", accept_lang,
        ])
        cmd.extend(["--merge-output-format", "mp4"])
    elif platform == "youtube":
        # web_safari отдаёт предсклеенные H.264 HLS-форматы 1080p/720p, у которых
        # нет поля filesize/filesize_approx — старый селектор их отбрасывал и падал
        # в 240p. manifest-filesize-approx проставляет приблизительный размер по
        # манифесту, чтобы фильтры filesize_approx работали. Клиенты
        # web_safari,android_vr,tv дают предпочитаемые avc1-форматы без DRM.
        cmd.extend([
            "--no-playlist",
            "--compat-options", "manifest-filesize-approx",
            "--extractor-args", "youtube:player_client=web_safari,android_vr,tv",
            "-f", (
                f"b[vcodec~='^avc1'][ext=mp4][height<=1080][filesize_approx<{settings.MAX_FILE_SIZE_MB - 2}M]/"
                f"b[vcodec~='^avc1'][ext=mp4][height<=720][filesize_approx<{settings.MAX_FILE_SIZE_MB - 2}M]/"
                f"bv*[ext=mp4][vcodec~='^avc1'][filesize_approx<{settings.MAX_FILE_SIZE_MB - 15}M]+ba[ext=m4a][filesize_approx<15M]/"
                f"bv*[ext=mp4][vcodec~='^avc1'][height<=480]+ba[ext=m4a]/"
                f"b[ext=mp4][height<=360][filesize_approx<{settings.MAX_FILE_SIZE_MB - 2}M]/"
                f"bv*[height<=240]+ba/b[height<=240]"
            ),
        ])
        cmd.extend(["--merge-output-format", "mp4"])
    else:
        # Предохранитель на будущее: url_parser сейчас отдаёт только 5 платформ
        # выше, поэтому сюда не попадаем. Оставляем универсальный mp4-селектор,
        # чтобы новая платформа не падала на пустой команде.
        cmd.extend(["--no-playlist", "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"])
        cmd.extend(["--merge-output-format", "mp4"])

    cmd.append(url)
    return cmd


def _find_downloaded_files(directory: Path, prefix: str) -> list[Path]:
    result = []
    for f in directory.iterdir():
        if f.is_file() and f.name.startswith(prefix):
            result.append(f)

    def _sort_key(p: Path) -> tuple[int, str]:
        # Extract numeric suffix after prefix: prefix_42.ext → 42
        rest = p.name[len(prefix):]
        m = re.search(r'_(\d+)', rest)
        return (int(m.group(1)) if m else 0, p.name)

    return sorted(result, key=_sort_key)


def _cleanup_glob(directory: Path, prefix: str) -> None:
    for f in directory.iterdir():
        if f.is_file() and f.name.startswith(prefix):
            try:
                os.unlink(f)
            except Exception:
                pass


def _sized_files(paths: list[Path]) -> list[tuple[Path, int]]:
    """Существующие непустые файлы вместе с размерами.

    Между `_find_downloaded_files` и `.stat()` файл может исчезнуть — успевает
    вклиниться фоновый подметальщик. Раньше `FileNotFoundError` улетал мимо
    `try` загрузчика и мимо `try` хендлера прямо в диспетчер: статус-сообщение
    висело вечно, в `download_log` не писалось ничего, файлы оставались на диске.
    """
    result: list[tuple[Path, int]] = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 0:
            result.append((path, size))
    return result


@dataclass
class GalleryDlRun:
    files: list[Path] = field(default_factory=list)
    stderr: str = ""
    returncode: int | None = None


async def _try_gallery_dl(url: str, filename: str) -> GalleryDlRun:
    with _ephemeral_cookies() as cookies_path:
        cmd = [
            "gallery-dl",
            "-D", str(DOWNLOAD_DIR),
            "-f", f"{filename}_{{num}}.{{extension}}",
        ]
        if cookies_path is not None:
            cmd.extend(["--cookies", str(cookies_path)])
        cmd.append(url)

        logger.info("Falling back to gallery-dl | url={}", url)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=settings.DOWNLOAD_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("gallery-dl timeout | url={}", url)
                process.kill()
                await process.wait()
                return GalleryDlRun(stderr="gallery-dl timeout")

            stderr_text = stderr.decode(errors="replace").strip()
            if process.returncode != 0:
                logger.warning(
                    "gallery-dl failed | code={} stderr={}",
                    process.returncode, stderr_text[:300],
                )
                return GalleryDlRun(stderr=stderr_text, returncode=process.returncode)

            return GalleryDlRun(
                files=_find_downloaded_files(DOWNLOAD_DIR, filename),
                stderr=stderr_text,
                returncode=process.returncode,
            )
        except Exception as exc:
            logger.exception("gallery-dl error: {}", exc)
            return GalleryDlRun(stderr=str(exc))


async def _try_gallery_dl_fallback(
    url: str, filename: str, outputs: list[tuple[str, str]]
) -> DownloadResult | None:
    run = await _try_gallery_dl(url, filename)
    # stderr gallery-dl копим ВСЕГДА — даже на успехе, чтобы при последующем
    # провале другой ветки его текст не пропал.
    if run.stderr:
        outputs.append(("gallery-dl", run.stderr))
    if not run.files:
        return None

    # _sized_files, а не голый .stat(): файл может исчезнуть между поиском и
    # проверкой размера, и FileNotFoundError отсюда улетает мимо всех try
    # прямо в диспетчер (H-9, закрыто Task 4 — не откатывать).
    sized_gd = _sized_files(run.files)
    if not sized_gd:
        return None

    valid_gd = [path for path, _ in sized_gd]
    total_size_mb = round(sum(size for _, size in sized_gd) / (1024 * 1024), 2)
    ext = valid_gd[0].suffix.lower()
    media_type = "image" if ext in IMAGE_EXTS else "video"
    logger.info("gallery-dl complete | files={} total_size={}MB", len(valid_gd), total_size_mb)

    if len(valid_gd) > 1:
        return DownloadResult(
            file_path=str(valid_gd[0]),
            file_paths=[str(f) for f in valid_gd],
            file_size_mb=total_size_mb,
            success=True,
            media_type=media_type,
        )
    return DownloadResult(
        file_path=str(valid_gd[0]),
        file_size_mb=total_size_mb,
        success=True,
        media_type=media_type,
    )


async def download_media(url: str, platform: str) -> DownloadResult:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    filename = uuid4().hex
    # Выводы всех запущенных утилит в порядке запуска — на них строится
    # сообщение об ошибке, если ни одна ветка не дала файлов.
    outputs: list[tuple[str, str]] = []
    output_path = DOWNLOAD_DIR / (filename + ".mp4")

    # Приоритетный gallery-dl: часть URL yt-dlp извлекает некорректно и теряет медиа —
    # Pinterest (картинки/доски), Instagram /p/ (смешанные фото+видео карусели, где
    # --ignore-no-formats-error молча роняет фото), TikTok /photo/ (слайдшоу, из
    # которого yt-dlp достаёт только аудио). Для них сразу пробуем gallery-dl; если
    # он ничего не вернул — чистим за собой и падаем в обычный путь yt-dlp ниже.
    # /reel/, /reels/, /tv/ у Instagram и /video/ у TikTok остаются на yt-dlp
    # (лучше качество видео) с уже существующим gallery-dl-fallback'ом.
    url_lower = url.lower()
    gallery_first = (
        platform == "pinterest"
        or (platform == "instagram" and "/p/" in url_lower)
        or (platform == "tiktok" and "/photo/" in url_lower)
    )
    if gallery_first:
        gd_result = await _try_gallery_dl_fallback(url, filename, outputs)
        if gd_result:
            return gd_result
        logger.info("gallery-dl primary returned nothing, falling back to yt-dlp | platform={} url={}", platform, url)
        _cleanup_glob(DOWNLOAD_DIR, filename)

    with _ephemeral_cookies() as cookies_path:
        if platform in ("pinterest", "instagram"):
            if platform == "instagram":
                output_template = DOWNLOAD_DIR / (filename + "_%(playlist_index)s.%(ext)s")
            else:
                output_template = DOWNLOAD_DIR / (filename + ".%(ext)s")
            cmd = _build_command(url, platform, output_template, cookies_path)
        else:
            cmd = _build_command(url, platform, output_path, cookies_path)

        logger.info("Starting download | platform={} url={}", platform, url)

        # Для TikTok делаем несколько попыток запуска yt-dlp: транзиторный WAF-челлендж
        # (rehydration / 403) часто проходит со второй попытки. Для остальных платформ
        # max_attempts=1 — поведение полностью прежнее.
        max_attempts = TIKTOK_MAX_ATTEMPTS if platform == "tiktok" else 1

        try:
            stdout = b""
            stderr = b""
            actual_files: list[Path] = []
            process = None
            for attempt in range(1, max_attempts + 1):
                process = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )

                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=settings.DOWNLOAD_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning("Download timeout | url={}", url)
                    process.kill()
                    await process.wait()
                    _cleanup_glob(DOWNLOAD_DIR, filename)
                    return DownloadResult(success=False, error_message="⏱ Таймаут: сервер не ответил за 120 секунд")

                actual_files = _find_downloaded_files(DOWNLOAD_DIR, filename)

                if actual_files or attempt == max_attempts:
                    break
                stderr_text = stderr.decode(errors="replace").lower()
                if platform == "tiktok" and any(m in stderr_text for m in TIKTOK_TRANSIENT_MARKERS):
                    logger.info("TikTok transient failure, retry {}/{} after {}s", attempt, max_attempts, TIKTOK_RETRY_DELAY)
                    _cleanup_glob(DOWNLOAD_DIR, filename)
                    await asyncio.sleep(TIKTOK_RETRY_DELAY)
                    continue
                break

            # Если файлы скачаны — это успех, даже если exit code != 0
            # (yt-dlp может вернуть code 1 из-за проблем с записью cookies, но файлы уже есть)
            if actual_files:
                sized_files = _sized_files(actual_files)
                valid_files = [path for path, _ in sized_files]
                if not valid_files:
                    stderr_text = stderr.decode(errors="replace").strip()
                    stdout_text = stdout.decode(errors="replace").strip()
                    full_output = stderr_text or stdout_text
                    logger.error("yt-dlp produced zero-size files | code={}", process.returncode)
                    _cleanup_glob(DOWNLOAD_DIR, filename)

                    outputs.append(("yt-dlp", full_output))
                    if platform in GALLERY_DL_FALLBACK_PLATFORMS:
                        gd_result = await _try_gallery_dl_fallback(url, filename, outputs)
                        if gd_result:
                            return gd_result

                    return DownloadResult(success=False, error_message=_parse_error(outputs, platform))

                total_size_mb = round(sum(size for _, size in sized_files) / (1024 * 1024), 2)
                logger.info("Download complete | files={} total_size={}MB", len(valid_files), total_size_mb)
                if process.returncode != 0:
                    stderr_text = stderr.decode(errors="replace").strip()
                    logger.warning("yt-dlp exited with code {} but files exist | stderr={}", process.returncode, stderr_text[:200])

                first_file = valid_files[0]
                ext = first_file.suffix.lower()
                media_type = "image" if ext in IMAGE_EXTS else "video"

                if len(valid_files) > 1:
                    return DownloadResult(
                        file_path=str(first_file),
                        file_paths=[str(f) for f in valid_files],
                        file_size_mb=total_size_mb,
                        success=True,
                        media_type=media_type,
                    )
                else:
                    return DownloadResult(
                        file_path=str(first_file),
                        file_size_mb=total_size_mb,
                        success=True,
                        media_type=media_type,
                    )

            stderr_text = stderr.decode(errors="replace").strip()
            stdout_text = stdout.decode(errors="replace").strip()
            full_output = stderr_text or stdout_text
            logger.error("yt-dlp failed | code={} stderr={}", process.returncode, full_output)

            outputs.append(("yt-dlp", full_output))
            if platform in GALLERY_DL_FALLBACK_PLATFORMS:
                gd_result = await _try_gallery_dl_fallback(url, filename, outputs)
                if gd_result:
                    return gd_result

            _cleanup_glob(DOWNLOAD_DIR, filename)
            return DownloadResult(success=False, error_message=_parse_error(outputs, platform))

        except Exception as exc:
            logger.exception("Unexpected download error: {}", exc)
            _cleanup_glob(DOWNLOAD_DIR, filename)
            # Экранируем текст исключения — уходит с parse_mode=HTML.
            return DownloadResult(success=False, error_message=f"Непредвиденная ошибка: {html.escape(str(exc))}")
