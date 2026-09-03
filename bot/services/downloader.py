from __future__ import annotations

import asyncio
import contextlib
import html
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
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


def _parse_error(stderr: str, platform: str) -> str:
    stderr_lower = stderr.lower()
    logger.debug("Parsing error | platform={} stderr={}", platform, stderr[:500])

    if "cookies" in stderr_lower or "session" in stderr_lower or "cookie" in stderr_lower:
        return "🍪 Ошибка авторизации: cookies устарели. Обратитесь к админу."

    if "login" in stderr_lower or "authentication" in stderr_lower:
        if platform == "instagram":
            return "🔐 Instagram: видео недоступно. Попробуй другую ссылку (Reels из публичных аккаунтов)."
        return f"🔐 {platform.capitalize()}: требуется авторизация."

    if "not found" in stderr_lower or "404" in stderr_lower:
        return "🔍 Видео не найдено. Возможно, оно удалено или ссылка неверная."

    if "private" in stderr_lower:
        return "🔒 Это приватное видео. Скачивание невозможно."

    if "requested format" in stderr_lower or "no video formats" in stderr_lower:
        return "🔄 Формат видео не поддерживается. Попробуй другую ссылку."

    if "geo" in stderr_lower or "country" in stderr_lower:
        return "🌍 Видео недоступно в текущем регионе."

    if any(phrase in stderr_lower for phrase in ["age-restricted", "age_gate", "age gate", "confirm your age", "age verification", "age_verification"]):
        return "🔞 Видео с возрастным ограничением."

    if "unsupported" in stderr_lower or "no video" in stderr_lower:
        return "❌ Ссылка не содержит видео или не поддерживается."

    if "max-filesize" in stderr_lower or "file is larger" in stderr_lower:
        return f"📦 Файл слишком большой (больше {settings.MAX_FILE_SIZE_MB} МБ)."

    if "rate" in stderr_lower or "too many" in stderr_lower:
        return "⏳ Слишком много запросов. Попробуй через минуту."

    short_err = stderr[:200] if len(stderr) > 200 else stderr
    # Экранируем stderr: сообщение уходит с parse_mode=HTML, а сырой вывод yt-dlp
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
    import re
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


async def _try_gallery_dl(url: str, filename: str) -> list[Path] | None:
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
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=settings.DOWNLOAD_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("gallery-dl timeout | url={}", url)
                process.kill()
                await process.wait()
                return None

            if process.returncode != 0:
                stderr_text = stderr.decode(errors="replace").strip()
                logger.warning("gallery-dl failed | code={} stderr={}", process.returncode, stderr_text[:300])
                return None

            files = _find_downloaded_files(DOWNLOAD_DIR, filename)
            if files:
                return files
            return None
        except Exception as exc:
            logger.exception("gallery-dl error: {}", exc)
            return None


async def _try_gallery_dl_fallback(url: str, filename: str) -> DownloadResult | None:
    gd_files = await _try_gallery_dl(url, filename)
    if not gd_files:
        return None

    valid_gd = [f for f in gd_files if f.stat().st_size > 0]
    if not valid_gd:
        return None

    total_size_mb = round(sum(f.stat().st_size for f in valid_gd) / (1024 * 1024), 2)
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
    else:
        return DownloadResult(
            file_path=str(valid_gd[0]),
            file_size_mb=total_size_mb,
            success=True,
            media_type=media_type,
        )


async def download_media(url: str, platform: str) -> DownloadResult:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    filename = uuid4().hex
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
        gd_result = await _try_gallery_dl_fallback(url, filename)
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
                valid_files = [f for f in actual_files if f.stat().st_size > 0]
                if not valid_files:
                    stderr_text = stderr.decode(errors="replace").strip()
                    stdout_text = stdout.decode(errors="replace").strip()
                    full_output = stderr_text or stdout_text
                    logger.error("yt-dlp produced zero-size files | code={}", process.returncode)
                    _cleanup_glob(DOWNLOAD_DIR, filename)

                    if platform in GALLERY_DL_FALLBACK_PLATFORMS:
                        gd_result = await _try_gallery_dl_fallback(url, filename)
                        if gd_result:
                            return gd_result

                    return DownloadResult(success=False, error_message=_parse_error(full_output, platform))

                total_size_mb = round(sum(f.stat().st_size for f in valid_files) / (1024 * 1024), 2)
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

            if platform in GALLERY_DL_FALLBACK_PLATFORMS:
                gd_result = await _try_gallery_dl_fallback(url, filename)
                if gd_result:
                    return gd_result

            _cleanup_glob(DOWNLOAD_DIR, filename)
            return DownloadResult(success=False, error_message=_parse_error(full_output, platform))

        except Exception as exc:
            logger.exception("Unexpected download error: {}", exc)
            _cleanup_glob(DOWNLOAD_DIR, filename)
            # Экранируем текст исключения — уходит с parse_mode=HTML.
            return DownloadResult(success=False, error_message=f"Непредвиденная ошибка: {html.escape(str(exc))}")
