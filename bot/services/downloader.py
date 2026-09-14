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
from bot.utils.log_guard import mask_secrets

DOWNLOAD_DIR = Path("/tmp/jw_downloads")
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
# .gif вынесен из изображений: sendPhoto/InputMediaPhoto сохраняет только первый
# кадр и молча теряет анимацию — отдавать такой файл нужно через sendAnimation
# (см. _media_type_for).
ANIMATION_EXTS = {".gif"}
ALLOWED_EXTS = VIDEO_EXTS | IMAGE_EXTS | ANIMATION_EXTS
# Промежуточные артефакты yt-dlp: отдельные DASH-дорожки вида `<prefix>.f137.mp4`
# имеют допустимое расширение (.mp4 ∈ VIDEO_EXTS) — одного белого списка мало,
# их дополнительно отсеивает _is_finished_media через этот шаблон.
_FRAGMENT_RE = re.compile(r"\.f\d+\.[A-Za-z0-9]+$")
GALLERY_DL_FALLBACK_PLATFORMS = {"instagram", "pinterest", "tiktok"}
# Потолок числа элементов на ОДНУ единицу квоты. Только доска/профиль/поиск
# Pinterest не ограничены платформой — 1723 элемента в живом тесте. Одиночный
# пин Pinterest, карусель Instagram (/p/, нативно ≤20) и слайдшоу TikTok
# (/photo/) так разрастись не могут — им дан отдельный, более высокий
# потолок (см. `_gallery_dl_item_limit`): фикс-раунд 1 нашёл, что общий
# низкий потолок на ВСЕ запуски молча обрезал карусели, которые раньше
# приезжали целиком, — чиним H-6 и тут же создаём новую регрессию.
GALLERY_DL_MAX_ITEMS = 10
GALLERY_DL_CAROUSEL_MAX_ITEMS = 20
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
    # Точное «N из M» из gallery-dl недостижимо (stderr не даёт надёжного
    # счётчика элементов доски) — но этих двух флагов достаточно, чтобы
    # следующий пакет решил, что сказать пользователю и списывать ли квоту:
    # partial — часть элементов не скачалась (ненулевой код при непустом
    # результате); truncated — результат уткнулся в потолок --range, за
    # пределом могли остаться ещё элементы. Проставляются только на
    # gallery-dl-пути (см. `_try_gallery_dl_fallback`); на yt-dlp — всегда
    # False, поведение не менялось.
    partial: bool = False
    truncated: bool = False


# ── Классификация ошибок загрузки ────────────────────────────────────────

# Классифицируем только те строки, которые сами утилиты пометили как ошибку:
# yt-dlp печатает «ERROR: ...», gallery-dl — «[extractor][error] ...».
# Прогресс, предупреждения и эхо аргументов в классификацию не попадают —
# именно из-за них раньше любой сбой объявлялся протуханием cookies.
#
# Исключение — обрыв по размеру файла: yt-dlp печатает его через to_screen
# («[download] File is larger than max-filesize (…). Aborting.»), БЕЗ
# маркера ERROR:, и без этой альтернативы строка отфильтровывалась начисто —
# правило too_large ниже никогда не получало шанса сработать (находка
# ревью фикс-раунда 1).
_ERROR_LINE_RE = re.compile(
    r"(?:^|\s)(?:ERROR:|\[[^\]\s]+\]\[error\]|error:)"
    r"|file is larger than max-?filesize",
    re.IGNORECASE,
)

# Для ПОКАЗА пользователю (в отличие от классификации выше) хотим больше
# контекста: `[warning]`-строка непосредственно перед `[error]` часто
# называет настоящую причину (DNS-сбой, HTTP-код), а сам `[error]` — только
# сухое следствие («API request failed»). Классификацию этим же фильтром
# сознательно не расширяем — `[warning]` слишком шумный источник для правил
# и вернул бы ровно тот риск ложных срабатываний, ради которого затевалась
# вся задача; для сырого текста в `<code>` это не риск, а польза.
_DISPLAY_LINE_RE = re.compile(
    r"(?:^|\s)(?:ERROR:|\[[^\]\s]+\]\[(?:error|warning)\]|error:)"
    r"|file is larger than max-?filesize",
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
        # Раньше стояло ПОСЛЕ not_found — «Video unavailable… blocked in
        # your country» ловилось словом «unavailable» как not_found раньше,
        # чем как гео-блок (находка ревью: специфичное правило обязано идти
        # раньше общего — тот самый принцип, который декларирует эта
        # задача). geo_block теперь стоит раньше.
        "geo_block",
        re.compile(
            r"geo[- ]?(?:restrict|block)"
            # Живой текст yt-dlp: «The uploader has NOT MADE this video
            # AVAILABLE IN YOUR country» — «not» и «available in your»
            # разделены несколькими словами, поэтому раньше не матчилось
            # вообще никак (было `not available (?:in|from) your ...` —
            # требовал их встык). Разрешаем разрыв в пределах предложения.
            r"|not\b[^.]{0,60}available (?:in|from) your (?:country|location|region)"
            r"|blocked in your (?:country|location|region)",
            re.IGNORECASE,
        ),
        "🌍 Видео недоступно в текущем регионе.",
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
    """Оставляет из вывода утилиты только строки, помеченные как ошибка.

    Только для КЛАССИФИКАЦИИ (`_parse_error`) — намеренно строгий фильтр.
    Для показа пользователю см. `_display_surface`.
    """
    lines = [ln.strip() for ln in text.splitlines() if _ERROR_LINE_RE.search(ln)]
    return "\n".join(lines)


def _display_surface(text: str) -> str:
    """Как `_error_surface`, но для показа пользователю: включает соседние
    `[warning]`-строки и «хвост» без собственного маркера сразу после
    отмеченной строки — yt-dlp иногда дописывает пояснение (подсказку про
    VPN) обычной строкой без ERROR:/[error] на ней самой. Дубликаты не
    возникают: если «хвост» сам оказывается маркерной строкой, добавлять
    его повторно не нужно — своя итерация цикла его и так подхватит.
    """
    lines = text.splitlines()
    keep: list[str] = []
    for i, ln in enumerate(lines):
        if not _DISPLAY_LINE_RE.search(ln):
            continue
        keep.append(ln.strip())
        if i + 1 < len(lines):
            tail = lines[i + 1].strip()
            if tail and not _DISPLAY_LINE_RE.search(tail):
                keep.append(tail)
    return "\n".join(keep)


def _pick_fallback(outputs: Sequence[tuple[str, str]]) -> str:
    for wanted in _SOURCE_PRIORITY:
        for name, text in outputs:
            if name != wanted:
                continue
            candidate = _display_surface(text) or text.strip()
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

    # Маскируем ПЕРЕД обрезкой и экранированием. Порядок важен в обе стороны:
    # обрезка «в лоб» до маскировки могла бы разрезать секрет пополам и
    # оставить читаемый хвост (сигнатуру CDN, кусок sessionid); html.escape
    # после маскировки испортил бы её же шаблоны (`&` → `&amp;` ломает
    # `[?&]token=...`). Живые утечки без этой строки: `&sig=`/`&access_token=`
    # в подписанных CDN-URL инстаграма, netscape-поле `sessionid<TAB>...`,
    # пути `/app/secrets/cookies.txt`, `/tmp/jw_cookies_*`, credentials
    # прокси в URL — любой пользователь бота вытягивал характеристики хоста.
    short_err = mask_secrets(_pick_fallback(outputs))[:200]
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
        # Раньше единственная ветка без фиксации контейнера: лучшая пара DASH
        # у Instagram часто vp9/opus (не mp4-совместимо), yt-dlp склеивал в
        # .mkv/.webm — расширение мимо IMAGE_EXTS, файл уходил как видео, и
        # Telegram отдавал невоспроизводимое вложение.
        cmd.extend(["--merge-output-format", "mp4"])
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


def _is_finished_media(path: Path) -> bool:
    """Готовый к отправке файл, а не промежуточный артефакт загрузчика.

    Белый список расширений (ALLOWED_EXTS) сам по себе уже отсекает
    `<...>.part`: gallery-dl (part=True по умолчанию) и yt-dlp пишут во
    временный `.part`, пока элемент не докачан целиком, и переименовывают в
    финальное имя только на завершении — суффикс `.part` не входит ни в одно
    из трёх множеств. При обрыве (сеть, наш kill() на таймауте) `.part` может
    остаться на диске; раньше без явного фильтра он разрешался в
    DownloadResult.file_paths как обычный файл (живая находка ревью
    фикс-раунда 1). `--no-part` не берём осознанно: он убрал бы именно тот
    сигнал, по которому отличаем целый файл от обрыва, и оборванный элемент
    лёг бы под финальным именем без маркера вообще.

    Одного белого списка мало: отдельные DASH-дорожки вида `<prefix>.f137.mp4`
    имеют допустимое расширение — их отсеивает _FRAGMENT_RE.
    """
    if path.suffix.lower() not in ALLOWED_EXTS:
        return False
    return not _FRAGMENT_RE.search(path.name)


def _media_type_for(path: Path) -> str:
    """Единый источник истины для типа медиа по расширению файла.

    Используется и внутри загрузчика (выбор ветки DownloadResult), и должен
    использоваться хендлером при отправке — чтобы классификация не
    расходилась по нескольким местам (см. ALLOWED_EXTS/ANIMATION_EXTS).
    """
    ext = path.suffix.lower()
    if ext in ANIMATION_EXTS:
        return "animation"
    if ext in IMAGE_EXTS:
        return "image"
    return "video"


def _find_downloaded_files(directory: Path, prefix: str) -> list[Path]:
    result = []
    for f in directory.iterdir():
        if not f.is_file() or not f.name.startswith(prefix):
            continue
        if not _is_finished_media(f):
            continue
        result.append(f)

    def _sort_key(p: Path) -> tuple[int, str]:
        # Числовой суффикс сразу после префикса: prefix_42.ext → 42
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


# Одиночный пин Pinterest — полный домен `.../pin/<id>/` или шортлинк
# `pin.it/<code>`. Всё остальное на Pinterest (доска, профиль, поиск) не
# ограничено платформой и получает низкий потолок — см. `_gallery_dl_item_limit`.
_PINTEREST_SINGLE_PIN_RE = re.compile(r"pinterest\.[a-z.]+/pin/|pin\.it/", re.IGNORECASE)


def _gallery_dl_item_limit(url: str, platform: str) -> int:
    """Потолок `--range`: зависит от ФОРМЫ ссылки, а не только платформы.

    Только доска/профиль/поиск Pinterest потенциально неограничены (1723
    элемента в живом тесте) — им низкий `GALLERY_DL_MAX_ITEMS`. Одиночный
    пин Pinterest, карусель Instagram (`/p/`, нативно ≤20 элементов) и
    слайдшоу TikTok (`/photo/`) так разрастись не могут: раньше общий
    потолок уходил во ВСЕ запуски gallery-dl и молча обрезал карусели,
    которые до фикса H-6 приезжали целиком, — чинили одну тихую потерю
    данных и тут же создавали другую (находка ревью фикс-раунда 1).
    """
    if platform == "pinterest" and not _PINTEREST_SINGLE_PIN_RE.search(url):
        return GALLERY_DL_MAX_ITEMS
    return GALLERY_DL_CAROUSEL_MAX_ITEMS


def _build_gallery_dl_cmd(url: str, platform: str, prefix: str, cookies_path: Path | None) -> list[str]:
    """Командная строка gallery-dl. Вынесена отдельно, чтобы её можно было
    проверить тестом без сети.

    `prefix` — наш uuid4-префикс. `{id}`/`{filename}` в шаблоне — это поля
    МЕТАДАННЫХ gallery-dl, а не наши переменные: двойные фигурные скобки в
    f-строке дают литеральные одинарные.

    `{id}` выбран вместо дефолтного для Pinterest `{filename}` сознательно
    (находка ревью фикс-раунда 1): `{filename}` — это хеш содержимого
    CDN-URL картинки, у двух РЕПИНОВ одной и той же картинки на одной доске
    он совпадает — второй пин разрешится в то же имя и будет пропущен как
    «уже существует» (тот же класс потери, что чинили изначально, только
    более редкий триггер). `{id}` — id самого пина, уникален всегда,
    независимо от содержимого. `{num}` стоит ПЕРЕД альтернативой: это
    порядок элемента ВНУТРИ поста (карусель 1..N) — если сдвинуть, сломается
    ключ сортировки в `_find_downloaded_files` (первое `_<число>` после
    префикса). `{id|filename|num}` — синтаксис альтернатив gallery-dl:
    первое непустое значение, на случай если экстрактор не проставил `id`.
    """
    cmd = [
        "gallery-dl",
        "-D", str(DOWNLOAD_DIR),
        "-f", f"{prefix}_{{num}}_{{id|filename|num}}.{{extension}}",
        "--range", f"1-{_gallery_dl_item_limit(url, platform)}",
    ]
    if cookies_path is not None:
        cmd.extend(["--cookies", str(cookies_path)])
    cmd.append(url)
    return cmd


async def _try_gallery_dl(url: str, platform: str, filename: str) -> GalleryDlRun:
    with _ephemeral_cookies() as cookies_path:
        cmd = _build_gallery_dl_cmd(url, platform, filename, cookies_path)

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
                # Гонка asyncio.wait_for: процесс может успеть завершиться
                # сам между истечением таймаута и этим вызовом — kill() на
                # уже мёртвом процессе бросает ProcessLookupError, из-за
                # которого _cleanup_glob ниже не выполнялся и обрезки
                # оставались на диске (поймано тестом на детерминированной
                # версии этой же гонки).
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
                # Обрезки после таймаута раньше оставались в tmpfs до
                # подметальщика и могли уйти пользователю как готовое медиа.
                _cleanup_glob(DOWNLOAD_DIR, filename)
                return GalleryDlRun(stderr="gallery-dl timeout")

            stderr_text = stderr.decode(errors="replace").strip()
            files = _find_downloaded_files(DOWNLOAD_DIR, filename)

            if process.returncode != 0:
                if files:
                    # Частичный сбой: часть элементов доски недоступна, но
                    # остальные уже на диске. Выбрасывать их — терять работу,
                    # за которую с пользователя уже списана квота.
                    logger.warning(
                        "gallery-dl exited with code {} but files exist | files={} stderr={}",
                        process.returncode, len(files), stderr_text[:300],
                    )
                else:
                    logger.warning(
                        "gallery-dl failed | code={} stderr={}",
                        process.returncode, stderr_text[:300],
                    )

            return GalleryDlRun(
                files=files, stderr=stderr_text, returncode=process.returncode
            )
        except Exception as exc:
            logger.exception("gallery-dl error: {}", exc)
            return GalleryDlRun(stderr=str(exc))


async def _try_gallery_dl_fallback(
    url: str, platform: str, filename: str, outputs: list[tuple[str, str]]
) -> DownloadResult | None:
    run = await _try_gallery_dl(url, platform, filename)
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
    media_type = _media_type_for(valid_gd[0])
    # См. DownloadResult.partial/.truncated: обе величины уже под рукой
    # ровно там, где их иначе выбросили бы.
    partial = run.returncode != 0 and bool(valid_gd)
    truncated = len(valid_gd) == _gallery_dl_item_limit(url, platform)
    logger.info(
        "gallery-dl complete | files={} total_size={}MB partial={} truncated={}",
        len(valid_gd), total_size_mb, partial, truncated,
    )

    if len(valid_gd) > 1:
        return DownloadResult(
            file_path=str(valid_gd[0]),
            file_paths=[str(f) for f in valid_gd],
            file_size_mb=total_size_mb,
            success=True,
            media_type=media_type,
            partial=partial,
            truncated=truncated,
        )
    return DownloadResult(
        file_path=str(valid_gd[0]),
        file_size_mb=total_size_mb,
        success=True,
        media_type=media_type,
        partial=partial,
        truncated=truncated,
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
        gd_result = await _try_gallery_dl_fallback(url, platform, filename, outputs)
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
                    # Та же гонка asyncio.wait_for, что и в gallery-dl-ветке
                    # (см. _try_gallery_dl): процесс может успеть завершиться
                    # сам между истечением таймаута и этим вызовом — kill()
                    # на уже мёртвом процессе бросает ProcessLookupError, и
                    # вместо честного «таймаут» пользователь получил бы
                    # «непредвиденная ошибка» из внешнего except ниже.
                    with contextlib.suppress(ProcessLookupError):
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
                        gd_result = await _try_gallery_dl_fallback(url, platform, filename, outputs)
                        if gd_result:
                            return gd_result

                    return DownloadResult(success=False, error_message=_parse_error(outputs, platform))

                total_size_mb = round(sum(size for _, size in sized_files) / (1024 * 1024), 2)
                logger.info("Download complete | files={} total_size={}MB", len(valid_files), total_size_mb)
                if process.returncode != 0:
                    stderr_text = stderr.decode(errors="replace").strip()
                    logger.warning("yt-dlp exited with code {} but files exist | stderr={}", process.returncode, stderr_text[:200])

                first_file = valid_files[0]
                media_type = _media_type_for(first_file)

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
                gd_result = await _try_gallery_dl_fallback(url, platform, filename, outputs)
                if gd_result:
                    return gd_result

            _cleanup_glob(DOWNLOAD_DIR, filename)
            return DownloadResult(success=False, error_message=_parse_error(outputs, platform))

        except Exception as exc:
            logger.exception("Unexpected download error: {}", exc)
            _cleanup_glob(DOWNLOAD_DIR, filename)
            # Маскируем и экранируем: текст исключения (например, ошибка ОС
            # с путём) уходит с parse_mode=HTML и может содержать тот же
            # класс секретов, что и обычное сообщение об ошибке (см. _parse_error).
            return DownloadResult(
                success=False,
                error_message=f"Непредвиденная ошибка: {html.escape(mask_secrets(str(exc)))}",
            )
