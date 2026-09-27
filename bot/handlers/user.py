from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InaccessibleMessage,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from loguru import logger

from bot.config import settings
from bot.db.engine import async_session
from bot.db.free_quota import FreeQuota, free_quota_status, refund_free_download, reserve_free_download
from bot.db.queries import get_or_create_user, increment_total_downloads, log_download
from bot.handlers.info import support_text
from bot.handlers.payments import CANCEL_HINT, get_invoice_link
from bot.keyboards.inline import (
    get_after_download_kb,
    get_back_to_menu_kb,
    get_help_kb,
    get_main_menu_kb,
    get_paywall_kb,
    get_status_kb,
    get_subscribe_kb,
)
from bot.services.cleanup import remove_file
from bot.services.disk_space import has_free_space
from bot.services.downloader import ANIMATION_EXTS, DownloadResult, IMAGE_EXTS, download_media
from bot.services.media_probe import MediaInfo, probe_media
from bot.services.progress_texts import (
    PREPARING_TEXT,
    QUEUED_TEXT,
    download_status_text,
    limit_line,
    limits_block,
    upload_status_text,
)
from bot.services.sending import ProbablyDeliveredError, SendVerdict, classify_send_failure, oversized_files
from bot.services.status_board import StatusBoard
from bot.services.upload_estimate import UploadRateTracker
from bot.services.ytdlp_progress import DownloadStatus, FormatPlan
from bot.utils.text import esc, format_wait
from bot.utils.url_parser import parse_url

router = Router(name="user")
download_semaphore = asyncio.Semaphore(3)
upload_rate = UploadRateTracker()
# Меньше — отправка занимает секунды, полоска прогресса не успевает быть полезной.
UPLOAD_PROGRESS_MIN_MB = 50

# Один пользователь — одна загрузка одновременно. Общего семафора на три слота
# мало: троттл в 3 с позволяет занять все три за девять секунд на всю длину
# загрузки (до DOWNLOAD_TIMEOUT), и остальные висят на «Скачиваю медиа...».
MAX_CONCURRENT_PER_USER = 1
user_active_downloads: dict[int, int] = {}

# Кто нажал «Скачать видео» и ещё не прислал ссылку. Раньше был set, который
# рос до конца жизни процесса: ушедшего пользователя из него ничто не убирало.
WAITING_TTL = 3600.0
waiting_for_url: dict[int, float] = {}

MEDIA_GROUP_CHUNK_SIZE = 5  # Telegram допускает до 10, но большие чанки (~10 МБ) вызывают таймауты
SEND_RETRY_DELAYS = (2, 5, 10)  # экспоненциальный backoff между повторными попытками отправки

# Беззвучное короткое видео — это «гифка»: в Telegram анимация и есть mp4 без
# звука. Pinterest и подобные отдают такие ролики немыми, и с reply_video они
# приходили плеером вместо зацикленной гифки. Потолок длительности нужен,
# чтобы длинная немая запись не превратилась в автоплей-луп без управления.
SILENT_VIDEO_AS_ANIMATION_MAX_SEC = 60.0

def _active_subscription_until(user) -> datetime | None:
    """Конец подписки в UTC, если она ещё действует; иначе None.

    SQLite отдаёт naive-datetime в UTC — без приведения сравнение с aware
    `now` падает. Раньше этот кусок был скопирован в трёх хендлерах.
    """
    until = user.subscription_until
    if until is None:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until if until > datetime.now(timezone.utc) else None


def _wait_text(quota: FreeQuota) -> str:
    """Через сколько откроется следующее бесплатное скачивание."""
    if quota.next_at is None:
        return format_wait(timedelta(0))
    return format_wait(quota.next_at - datetime.now(timezone.utc))


def _free_left_text(quota: FreeQuota) -> str:
    return f"осталось <b>{quota.left} из {settings.FREE_DOWNLOADS_PER_DAY}</b> на сутки"


def _quota_line(quota: FreeQuota, has_subscription: bool) -> str:
    """Одна строка о правах пользователя для приветствия и помощи."""
    if has_subscription:
        return "👑 Подписка активна — скачивай без ограничений."
    if quota.left > 0:
        return f"🎁 Бесплатно: {_free_left_text(quota)}."
    return (
        "⏳ Бесплатные на сутки закончились. "
        f"Следующее — через <b>{_wait_text(quota)}</b>. С подпиской — без ограничений."
    )


def _welcome_text(quota: FreeQuota, has_subscription: bool) -> str:
    return (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Я — бот для скачивания видео из соцсетей.\n\n"
        "🌐 <b>Поддерживаемые платформы:</b>\n"
        "├ 📸 Instagram\n"
        "├ 🎵 TikTok\n"
        "├ 📘 Facebook\n"
        "├ 📌 Pinterest\n"
        "└ 📺 YouTube\n\n"
        f"{limit_line(settings.MAX_FILE_SIZE_MB)}\n\n"
        f"{_quota_line(quota, has_subscription)}\n\n"
        "Выбери действие 👇"
    )


def _help_text(quota: FreeQuota, has_subscription: bool) -> str:
    return (
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми <b>«Скачать видео»</b>\n"
        "2️⃣ Отправь ссылку на видео\n"
        "3️⃣ Дождись файла — бот покажет, сколько осталось\n\n"
        "🌐 <b>Платформы:</b> Instagram, TikTok, Facebook, Pinterest, YouTube\n\n"
        f"{limits_block(settings.MAX_FILE_SIZE_MB)}\n\n"
        f"{_quota_line(quota, has_subscription)}"
    )


def _status_text(quota: FreeQuota, sub_until: datetime | None, total_downloads: int) -> str:
    sub_text = "✅ до " + sub_until.strftime("%d.%m.%Y") if sub_until else "❌ Не активна"
    free_line = f"🎟 Бесплатно: {_free_left_text(quota)}"
    if quota.left == 0:
        free_line += f"\n⏳ Следующее — через <b>{_wait_text(quota)}</b>"
    return (
        f"📊 <b>Твой профиль:</b>\n\n"
        f"{free_line}\n"
        f"👑 Подписка: <b>{sub_text}</b>\n"
        f"📥 Всего скачано: <b>{total_downloads}</b>"
    )


def _limit_reached_text(quota: FreeQuota) -> str:
    return (
        "🚫 Бесплатные скачивания на сутки закончились.\n"
        f"Следующее откроется через <b>{_wait_text(quota)}</b>.\n"
        "С подпиской — без ограничений 👇"
    )


def _incoming_text(message) -> str:
    """Текст сообщения или подпись к медиа. Раньше хендлер смотрел только
    в .text, поэтому бот молчал на фото со ссылкой в подписи."""
    return message.text or message.caption or ""


def _is_admin(user_id: int) -> bool:
    return user_id == settings.ADMIN_ID


def _classify(path_str: str) -> str:
    """"animation" | "image" | "video" по расширению файла.

    Расширения берутся из bot/services/downloader.py: раньше здесь был свой
    кортеж без точек, и он разъехался с загрузчиком на .gif.
    """
    ext = Path(path_str).suffix.lower()
    if ext in ANIMATION_EXTS:
        return "animation"
    if ext in IMAGE_EXTS:
        return "image"
    return "video"


def _try_take_user_slot(user_id: int) -> bool:
    """Занимает слот загрузки. False — у пользователя уже идёт загрузка."""
    if user_active_downloads.get(user_id, 0) >= MAX_CONCURRENT_PER_USER:
        return False
    user_active_downloads[user_id] = user_active_downloads.get(user_id, 0) + 1
    return True


def _release_user_slot(user_id: int) -> None:
    left = user_active_downloads.get(user_id, 0) - 1
    if left > 0:
        user_active_downloads[user_id] = left
    else:
        user_active_downloads.pop(user_id, None)


def _mark_waiting(user_id: int) -> None:
    """Отмечает ожидание ссылки и попутно вытесняет протухшие записи
    (тот же приём, что в bot/middlewares/throttle.py:28-34)."""
    now = time.monotonic()
    waiting_for_url[user_id] = now
    stale = [uid for uid, ts in waiting_for_url.items() if (now - ts) > WAITING_TTL]
    for uid in stale:
        del waiting_for_url[uid]


def _is_waiting(user_id: int) -> bool:
    ts = waiting_for_url.get(user_id)
    if ts is None:
        return False
    if (time.monotonic() - ts) > WAITING_TTL:
        del waiting_for_url[user_id]
        return False
    return True


def _download_failed_text(error_message: str | None) -> str:
    """error_message приходит из загрузчика УЖЕ экранированным
    (downloader.py:77 и :443 прогоняют текст через html.escape).
    Экранировать второй раз нельзя — пользователь увидит «&amp;lt;»."""
    return f"❌ <b>Не удалось скачать</b>\n{error_message or 'Причина неизвестна'}"


def _media_caption(platform: str, kind: str) -> str:
    """Подпись к отправляемому медиа. kind: "video" | "image" | "animation" | "album"."""
    titles = {"video": "Видео", "image": "Фото", "animation": "GIF", "album": "Медиа"}
    return f"✅ {titles.get(kind, 'Медиа')} из {esc(platform.capitalize())}"


def _url_for_log(url: str) -> str:
    """схема://хост/путь — без query и без fragment, для логов (не для БД).

    Fix round 3, N4: `mask_secrets` (bot/utils/log_guard.py, чужое владение)
    — это АЛЛОУЛИСТ имён query-параметров (~14 штук), а не эвристика по
    форме значения. Что в список не входит (например TikTok `_t`,
    `sec_user_id`, Meta `mibextid`, Pinterest `invite_code`) — уходит в лог
    дословно, и это подтверждено живым замером. Секреты живут именно в
    query-строке; для диагностики падения (какая платформа, какой ресурс)
    query не нужен вовсе. Поэтому в лог идёт голый путь, а не борьба за
    полноту чужого аллоулиста. `mask_secrets` всё равно применяется поверх
    (глобальный патчер loguru) — если что-то похожее на секрет всё же
    окажется в пути, вторым рубежом его добьёт она.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


NOT_MODIFIED_MARKER = "message is not modified"


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    """«Текст и клавиатура уже такие» — это успех, а не ошибка."""
    return NOT_MODIFIED_MARKER in str(exc).lower()


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    """Правит сообщение под кнопкой, а если это невозможно — шлёт новое.

    Прежний фолбэк повторял ТОТ ЖЕ текст через callback.message.answer(): если
    причина отказа была в самой разметке, вторая попытка падала так же, а
    исключение уходило наружу. Плюс callback.message бывает InaccessibleMessage
    или None (сообщение старше 48 часов) — тогда падали обе попытки сразу.
    Поэтому доступность проверяем заранее, а фолбэк шлём через bot.send_message.
    """
    msg = callback.message
    editable = msg is not None and not isinstance(msg, InaccessibleMessage)
    chat_id = msg.chat.id if msg is not None else callback.from_user.id

    if editable:
        try:
            await msg.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if _is_not_modified(exc):
                return
            logger.debug("edit_text отклонён, шлём новым сообщением | error={}", exc)
        except TelegramForbiddenError:
            logger.info("Пользователь заблокировал бота | user={}", callback.from_user.id)
            return
        except Exception as exc:
            logger.warning("edit_text упал неожиданно | error={}", exc)

    try:
        await callback.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup
        )
    except TelegramForbiddenError:
        logger.info("Пользователь заблокировал бота | user={}", callback.from_user.id)
    except Exception as exc:
        logger.warning(
            "Не удалось доставить экран | user={} error={}", callback.from_user.id, exc
        )


MAX_SEND_ATTEMPTS = len(SEND_RETRY_DELAYS) + 1

# Что вообще может случиться при отправке и стоит разбирать через
# classify_send_failure. TelegramEntityTooLarge — подкласс TelegramNetworkError,
# но перечислен явно для читаемости except-кортежа.
_RETRYABLE_SEND_ERRORS = (
    TelegramEntityTooLarge,
    TelegramRetryAfter,
    TelegramNetworkError,
    asyncio.TimeoutError,
    aiohttp.ClientError,
)


def _retry_delay(exc: BaseException, attempt: int) -> float:
    """Пауза перед повтором: у TelegramRetryAfter Telegram сам называет её."""
    if isinstance(exc, TelegramRetryAfter):
        return exc.retry_after
    return SEND_RETRY_DELAYS[attempt]


async def _handle_send_failure(exc: BaseException, elapsed: float, attempt: int) -> None:
    """Разбирает вердикт classify_send_failure для одной неудачной попытки.

    RETRY — спит и возвращается (вызывающий цикл повторит попытку). PROBABLY_DELIVERED
    трактуется как доставка только в локальном режиме — у нашего telegram-bot-api
    зашит IDLE_TIMEOUT, и обрыв после него бывает уже случившейся отдачей. В
    облаке такого таймаута нет, там тот же обрыв — обычный сетевой сбой. Во всех
    остальных случаях (GIVE_UP/TOO_LARGE и облачный PROBABLY_DELIVERED) поднимает
    исходное исключение как есть.
    """
    verdict = classify_send_failure(exc, elapsed, attempt, MAX_SEND_ATTEMPTS)
    if verdict is SendVerdict.RETRY:
        delay = _retry_delay(exc, attempt)
        logger.warning(
            "Сбой при отправке медиа, повтор через {}с | попытка={}/{} error={}",
            delay, attempt + 1, MAX_SEND_ATTEMPTS, exc,
        )
        await asyncio.sleep(delay)
        return
    if verdict is SendVerdict.PROBABLY_DELIVERED and settings.USE_LOCAL_BOT_API:
        logger.warning(
            "Отправка оборвана после {:.1f}с — файл, скорее всего, доставлен | попытка={}",
            elapsed, attempt + 1,
        )
        raise ProbablyDeliveredError() from exc
    raise exc


async def _send_with_retry(send_coro_factory):
    """Выполняет send_coro_factory() с ретраем при временных сетевых сбоях.

    Классификация исхода — bot.services.sending.classify_send_failure:
    TelegramEntityTooLarge никогда не ретраится, а обрыв дольше
    PROBABLY_DELIVERED_AFTER_SEC считается вероятной доставкой (сервер сам
    закрывает соединение по серверному IDLE_TIMEOUT, но файл мог полностью
    уйти получателю) — повтор в этом случае дал бы дубль в чате.
    """
    for attempt in range(MAX_SEND_ATTEMPTS):
        started = time.monotonic()
        try:
            result = await send_coro_factory()
        except _RETRYABLE_SEND_ERRORS as e:
            await _handle_send_failure(e, time.monotonic() - started, attempt)
            continue
        logger.info(
            "Отправка в Telegram завершена | elapsed={:.1f}s attempt={}",
            time.monotonic() - started, attempt + 1,
        )
        return result


async def _reserve_quota(session, tg_user) -> tuple[int, bool, bool, int | None]:
    """Первый шаг загрузки: пользователь, бан, подписка и бронь бесплатного скачивания.

    Возвращает (user_id, is_banned, has_subscription, reservation_id). Бронь —
    в той же транзакции, что и чтение: иначе между чтением и списанием
    помещаются параллельные загрузки того же юзера. Забаненному и подписчику
    не бронируется (reservation_id = None): первому нельзя, второму не нужно.
    """
    user = await get_or_create_user(session, tg_user)
    has_subscription = _active_subscription_until(user) is not None
    if user.is_banned or has_subscription:
        return user.id, user.is_banned, has_subscription, None
    reservation_id = await reserve_free_download(session, user.id)
    return user.id, user.is_banned, has_subscription, reservation_id


async def _user_quota_state(tg_user) -> tuple[FreeQuota, bool]:
    """(бесплатный остаток, активна ли подписка) — одна короткая транзакция."""
    async with async_session() as session, session.begin():
        user = await get_or_create_user(session, tg_user)
        has_subscription = _active_subscription_until(user) is not None
        quota = await free_quota_status(session, user.id)
    return quota, has_subscription


def _quota_action(reserved: bool, download_ok: bool, media_sent_count: int) -> str:
    """Что сделать с зарезервированной единицей: "keep" или "refund".

    Возвращаем только если пользователь не получил ничего: либо провалилась
    загрузка, либо не ушёл ни один файл. Частично доставленный альбом не
    возвращается — медиа у пользователя уже есть.
    """
    if not reserved:
        return "keep"
    if not download_ok:
        return "refund"
    if media_sent_count == 0:
        return "refund"
    return "keep"


async def _refund_quota(reservation_id: int) -> None:
    """Возврат брони отдельной короткой транзакцией.

    Падение возврата не должно ронять хендлер: пользователь уже увидел ошибку,
    а потерянное скачивание — меньшее зло, чем необработанное исключение.
    """
    try:
        async with async_session() as session, session.begin():
            await refund_free_download(session, reservation_id)
    except Exception as exc:
        logger.error("Не удалось вернуть бесплатное скачивание | reservation={} error={}", reservation_id, exc)


LOW_DISK_TEXT = "⚠️ Бот временно не может скачивать. Попробуй через несколько минут."
TOO_LARGE_FOR_TELEGRAM_TEXT = "📦 Telegram не принял файл: он слишком большой."
PROBABLY_DELIVERED_TEXT = (
    "📤 <b>Файл большой — Telegram ещё обрабатывает его.</b>\n"
    "Скорее всего, он появится в этом чате в ближайшие минуты. "
    "Если через 15 минут его нет — пришли ссылку ещё раз."
)


def _result_paths(dl_result: DownloadResult) -> list[str]:
    """Все пути результата без дублей — та же логика, что раньше собиралась
    прямо в `finally` (и отправленные, и отклонённые файлы)."""
    paths = list(dl_result.file_paths or [])
    if dl_result.file_path and dl_result.file_path not in paths:
        paths.append(dl_result.file_path)
    return paths


async def _record_failure(db_user_id: int, url: str, platform: str, reservation_id: int | None) -> None:
    """Возврат брони и запись неуспеха в лог — тот же порядок, что в трёх
    старых копиях этой логики выше по файлу (Этап 2 их не трогает)."""
    if _quota_action(reservation_id is not None, download_ok=False, media_sent_count=0) == "refund":
        await _refund_quota(reservation_id)
    try:
        async with async_session() as session, session.begin():
            await log_download(session, db_user_id, url, platform, "failed")
    except Exception as log_exc:
        logger.error("Не удалось записать лог загрузки | user={} error={}", db_user_id, log_exc)


def _oversize_text(actual_mb: float, limit_mb: int) -> str:
    return (
        "📦 <b>Файл слишком большой</b>\n"
        f"Получилось {actual_mb:.0f} МБ, а отправить можно до {limit_mb} МБ. "
        "Попробуй видео покороче."
    )


async def _show_status(status_msg, text: str) -> None:
    """Best-effort правка статус-сообщения: любой сбой не должен мешать
    отправке — тесты нарочно подсовывают статусы, у которых edit_text падает."""
    try:
        await status_msg.edit_text(text)
    except Exception as exc:
        logger.debug("Не удалось обновить статус-сообщение | error={}", exc)


async def _reject_oversized(
    status_msg, dl_result: DownloadResult, oversized: list[tuple[str, float]]
) -> None:
    """Отказ по размеру: статус пользователю и уборка всех путей результата."""
    actual_mb = max(size for _, size in oversized)
    await _show_status(status_msg, _oversize_text(actual_mb, settings.MAX_FILE_SIZE_MB))
    for path in _result_paths(dl_result):
        await remove_file(path)


async def _download_with_progress(
    board: StatusBoard, url: str, platform: str
) -> tuple[DownloadResult, FormatPlan | None]:
    """Скачивает медиа, показывая план формата и проценты на доске статуса.

    Последний статус от `download_media` хранится в замыкании — рендер,
    вызываемый тикером доски, каждый раз читает именно его, а не спрашивает
    yt-dlp напрямую (обновления приходят синхронно из цикла чтения stdout).
    """
    latest: DownloadStatus | None = None

    def remember(status: DownloadStatus) -> None:
        nonlocal latest
        latest = status

    if download_semaphore.locked():
        await board.show(QUEUED_TEXT)

    def render() -> str:
        return download_status_text(latest, settings.MAX_FILE_SIZE_MB)

    async with download_semaphore:
        async with board.ticking(render):
            dl_result = await download_media(url, platform, on_status=remember)

    return dl_result, latest.plan if latest else None


def _wants_upload_progress(dl_result: DownloadResult) -> bool:
    """Полоска отправки — только для одиночного видео от UPLOAD_PROGRESS_MIN_MB:
    альбомы и мелкие файлы уходят за секунды, полоска не успевает пригодиться."""
    is_single = not dl_result.file_paths or len(dl_result.file_paths) <= 1
    return (
        is_single
        and dl_result.media_type == "video"
        and (dl_result.file_size_mb or 0) >= UPLOAD_PROGRESS_MIN_MB
    )


def _upload_renderer(dl_result: DownloadResult, plan: FormatPlan | None) -> Callable[[], str]:
    """Рендер статуса отправки. Часы — модульный `time.monotonic` (не
    захваченный на момент вызова объект): тесты подменяют `U.time` целиком."""
    limit_mb = settings.MAX_FILE_SIZE_MB
    size_mb = dl_result.file_size_mb
    if not _wants_upload_progress(dl_result):
        return lambda: upload_status_text(plan, size_mb, None, None, limit_mb)

    expected = upload_rate.expected_seconds(size_mb)
    started = time.monotonic()

    def render() -> str:
        elapsed = time.monotonic() - started
        return upload_status_text(plan, size_mb, elapsed, expected, limit_mb)

    return render


def _video_meta(info: MediaInfo | None) -> dict[str, int]:
    """width/height/duration для reply_video — только положительные значения.

    Без этого 1.5-гигабайтное видео нельзя смотреть, пока оно не скачается
    целиком: supports_streaming нужен вместе с этими тремя полями.
    """
    if info is None:
        return {}
    meta: dict[str, int] = {}
    if info.width > 0:
        meta["width"] = info.width
    if info.height > 0:
        meta["height"] = info.height
    if info.duration > 0:
        meta["duration"] = round(info.duration)
    return meta


async def _record_success(db_user_id: int, url: str, platform: str, size_mb: float | None) -> None:
    """Инкремент счётчика и success-лог одной транзакцией.

    Вынесено из успешной ветки `_process_download` без изменения поведения —
    её теперь вызывает и обычный успех, и «вероятно доставлено».
    """
    try:
        async with async_session() as session, session.begin():
            await increment_total_downloads(session, db_user_id)
            await log_download(session, db_user_id, url, platform, "success", size_mb)
    except Exception as log_exc:
        logger.error("Не удалось записать успешную загрузку | user={} error={}", db_user_id, log_exc)


async def _finish_probably_delivered(
    status_msg, db_user_id: int, url: str, platform: str, size_mb: float | None
) -> None:
    """Сервер оборвал соединение после долгой отдачи: файл, скорее всего, уже
    у пользователя. Квота не возвращается — повтор был бы дублем, а не помощью."""
    await _record_success(db_user_id, url, platform, size_mb)
    await _show_status(status_msg, PROBABLY_DELIVERED_TEXT)
    logger.warning(
        "Вероятно доставлено, повтор не выполняется | user={} url={}",
        db_user_id, _url_for_log(url),
    )


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    quota, has_subscription = await _user_quota_state(message.from_user)
    is_admin = _is_admin(message.from_user.id)
    await message.answer(
        _welcome_text(quota, has_subscription),
        reply_markup=get_main_menu_kb(is_admin=is_admin),
    )


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    waiting_for_url.pop(callback.from_user.id, None)
    quota, has_subscription = await _user_quota_state(callback.from_user)
    is_admin = _is_admin(callback.from_user.id)
    await _safe_edit(
        callback,
        _welcome_text(quota, has_subscription),
        reply_markup=get_main_menu_kb(is_admin=is_admin),
    )


@router.callback_query(F.data == "menu:download")
async def cb_download(callback: CallbackQuery) -> None:
    await callback.answer()
    _mark_waiting(callback.from_user.id)
    await _safe_edit(
        callback,
        "📥 <b>Скачивание видео</b>\n\n"
        "Отправь мне ссылку на видео из:\n"
        "📸 Instagram • 🎵 TikTok • 📘 Facebook • 📌 Pinterest • 📺 YouTube\n\n"
        "⬇️ Жду ссылку...",
        reply_markup=get_back_to_menu_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.callback_query(F.data == "menu:status")
async def cb_status(callback: CallbackQuery) -> None:
    await callback.answer()
    async with async_session() as session, session.begin():
        user = await get_or_create_user(session, callback.from_user)
        sub_until = _active_subscription_until(user)
        total_downloads = user.total_downloads
        quota = await free_quota_status(session, user.id)
    await _safe_edit(
        callback,
        _status_text(quota, sub_until, total_downloads),
        reply_markup=get_status_kb(is_admin=_is_admin(callback.from_user.id)),
    )


PAYMENT_UNAVAILABLE_TEXT = "⚠️ Оплата временно недоступна, попробуй позже."


def _subscribe_text(sub_until: datetime | None) -> str:
    if sub_until is not None:
        return (
            f"👑 <b>Подписка активна</b> до <b>{sub_until.strftime('%d.%m.%Y')}</b>.\n\n"
            f"Если оформлена звёздами — продлится сама; {CANCEL_HINT}."
        )
    return (
        "👑 <b>Подписка</b>\n\n"
        f"Безлимитные скачивания на 30 дней — <b>{settings.SUBSCRIPTION_PRICE_STARS} ⭐</b>.\n"
        f"Продлевается автоматически каждые 30 дней; {CANCEL_HINT}.\n\n"
        "Оплачивая, ты принимаешь условия: /terms"
    )


@router.callback_query(F.data == "menu:subscribe")
async def cb_subscribe(callback: CallbackQuery) -> None:
    """Кнопки покупки при активной подписке нет: Telegram разрешает одному
    человеку несколько подписок сразу — это было бы двойное списание."""
    await callback.answer()
    is_admin = _is_admin(callback.from_user.id)
    async with async_session() as session, session.begin():
        sub_until = _active_subscription_until(await get_or_create_user(session, callback.from_user))
    if sub_until is not None:
        await _safe_edit(callback, _subscribe_text(sub_until), reply_markup=get_back_to_menu_kb(is_admin=is_admin))
        return
    try:
        link = await get_invoice_link(callback.bot)
    except Exception as exc:
        logger.error("Не удалось создать ссылку на оплату | error={}", exc)
        await _safe_edit(callback, PAYMENT_UNAVAILABLE_TEXT, reply_markup=get_back_to_menu_kb(is_admin=is_admin))
        return
    await _safe_edit(
        callback,
        _subscribe_text(None),
        reply_markup=get_subscribe_kb(link, settings.SUBSCRIPTION_PRICE_STARS, is_admin=is_admin),
    )


@router.callback_query(F.data == "menu:support")
async def cb_support(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(
        callback,
        support_text(),
        reply_markup=get_back_to_menu_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.answer()
    quota, has_subscription = await _user_quota_state(callback.from_user)
    await _safe_edit(
        callback,
        _help_text(quota, has_subscription),
        reply_markup=get_help_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.message(F.text | F.caption)
async def handle_url(message: Message) -> None:
    user_id = message.from_user.id
    result = parse_url(_incoming_text(message))

    if result is None and _is_waiting(user_id):
        await message.answer(
            "🔗 Это не похоже на ссылку. Отправь ссылку из Instagram, TikTok, "
            "Facebook, Pinterest или YouTube.",
            reply_markup=get_back_to_menu_kb(is_admin=_is_admin(user_id)),
        )
        return

    if result is None:
        if message.text is None:
            # Медиа без ссылки в подписи — молчим, чтобы не отвечать меню на
            # каждое пересланное изображение.
            return
        await message.answer(
            "Выбери действие 👇",
            reply_markup=get_main_menu_kb(is_admin=_is_admin(user_id)),
        )
        return

    url, platform = result
    waiting_for_url.pop(user_id, None)

    # Слот берём ДО резервирования квоты: отказ не должен стоить единицы.
    if not _try_take_user_slot(user_id):
        await message.reply(
            "⏳ Я ещё качаю твою предыдущую ссылку. Дождись её и пришли следующую."
        )
        return
    try:
        await _process_download(message, url, platform)
    finally:
        _release_user_slot(user_id)


async def _process_download(message: Message, url: str, platform: str) -> None:
    user_id = message.from_user.id

    if not has_free_space(Path(settings.DOWNLOAD_ROOT), settings.MIN_FREE_DISK_GB):
        # Раньше отвалившийся USB-диск ловился только на записи файла: Docker
        # успевал создать каталог загрузок на eMMC (свободно 3.9 ГБ), и одна
        # гигабайтная загрузка укладывала весь хост вместе с базой. Квоту не
        # трогаем — до неё дело ещё не дошло.
        logger.error(
            "Недостаточно места на диске, загрузка отклонена | user={} path={}",
            user_id, settings.DOWNLOAD_ROOT,
        )
        try:
            await message.reply(LOW_DISK_TEXT)
        except Exception as exc:
            logger.info("Не удалось предупредить о нехватке места | user={} error={}", user_id, exc)
        return

    # C-1 шаг 1: одна короткая транзакция читает пользователя И резервирует
    # бесплатное скачивание. Раньше остаток читался здесь, а списывался после аплоада.
    async with async_session() as session, session.begin():
        db_user_id, is_banned, has_subscription, reservation_id = await _reserve_quota(
            session, message.from_user
        )

    if is_banned:
        # Резервирования не было — возвращать нечего.
        await message.reply("🚫 Ваш аккаунт заблокирован. Обратитесь к администратору.")
        return

    if not has_subscription and reservation_id is None:
        quota, _ = await _user_quota_state(message.from_user)
        await message.answer(
            _limit_reached_text(quota),
            reply_markup=get_paywall_kb(is_admin=_is_admin(message.from_user.id)),
        )
        return

    # Fix round 1, п.3 (окно 1/3): между резервированием и этим вызовом юзер
    # мог заблокировать бота в ту же секунду — раньше единица терялась молча.
    try:
        status_msg = await message.reply(PREPARING_TEXT)
    except Exception as e:
        # Fix round 2, N1: было (TelegramBadRequest, TelegramForbiddenError)
        # — узкий except пропускал TelegramNetworkError/TelegramRetryAfter
        # (сетевой сбой или 429 не менее вероятны тут, чем блокировка бота),
        # и они улетали из хендлера необработанными, унося единицу с собой.
        # Возврат и return корректны для ЛЮБОГО исключения на этом шаге —
        # дальше по коду ничего не сделано и делать нечего.
        logger.info(
            "Не удалось отправить статус-сообщение | user={} error={}", user_id, e
        )
        if _quota_action(reservation_id is not None, download_ok=False, media_sent_count=0) == "refund":
            await _refund_quota(reservation_id)
        return

    board = StatusBoard(status_msg)

    # Fix round 1, п.3 (окно 2/3): download_media НЕ гарантирует возврат
    # DownloadResult при любом исходе — до её собственного try (downloader.py)
    # успевают отработать mkdir/gallery-dl fallback/ephemeral cookies, и там
    # может прилететь, например, OSError(ENOSPC) при заполненном диске.
    # Скачивание — БЕЗ открытой db-сессии, чтобы не держать SQLite write-lock
    # на всю длину download+upload.
    try:
        dl_result, plan = await _download_with_progress(board, url, platform)
    except Exception:
        # Fix round 2, N4: logger.exception (не .error) — тянет traceback, а
        # не только str(e); на программной ошибке из глубины downloader.py
        # (AttributeError и т.п.) раньше в логе оставалось буквально
        # "'NoneType' object has no attribute ...' без единого шанса
        # локализовать место. user=/url=/platform= — как у соседних
        # диагностических строк в этом файле.
        logger.exception(
            "download_media упал до собственной обработки ошибок | user={} url={} platform={}",
            db_user_id, _url_for_log(url), platform,
        )
        if _quota_action(reservation_id is not None, download_ok=False, media_sent_count=0) == "refund":
            await _refund_quota(reservation_id)
        try:
            async with async_session() as session, session.begin():
                await log_download(session, db_user_id, url, platform, "failed")
        except Exception as log_exc:
            logger.error(
                "Не удалось записать лог загрузки | user={} error={}", db_user_id, log_exc
            )
        try:
            await status_msg.edit_text(
                "❌ <b>Не удалось скачать</b>\nПроизошла внутренняя ошибка. Попробуй ещё раз."
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        return

    if not dl_result.success:
        # Fix round 1, п.3 (окно 3/3): возврат единицы — ПЕРЕД записью в лог.
        # Неудачная транзакция лога (например SQLite "database is locked",
        # штатный исход под нагрузкой) не должна стоить пользователю
        # оплаченной попытки; сама транзакция лога дополнительно обёрнута —
        # её падение не должно рушить хендлер после того как возврат уже
        # сделан.
        if _quota_action(reservation_id is not None, download_ok=False, media_sent_count=0) == "refund":
            await _refund_quota(reservation_id)
        try:
            async with async_session() as session, session.begin():
                await log_download(session, db_user_id, url, platform, "failed")
        except Exception as log_exc:
            logger.error(
                "Не удалось записать лог загрузки | user={} error={}", db_user_id, log_exc
            )
        try:
            await status_msg.edit_text(_download_failed_text(dl_result.error_message))
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        return

    # Гейт размера: `--max-filesize` внутри yt-dlp проверяет дорожки по
    # отдельности, и склейка может выйти больше лимита. Ловим ПОСЛЕ загрузки,
    # по фактическому размеру готового файла — до отправки, чтобы не тратить
    # минуты на заведомо отклоняемую отдачу.
    oversized = oversized_files(_result_paths(dl_result), settings.MAX_FILE_SIZE_MB)
    if oversized:
        await _record_failure(db_user_id, url, platform, reservation_id)
        await _reject_oversized(status_msg, dl_result, oversized)
        return

    media_total_count = len(dl_result.file_paths) if dl_result.file_paths else 1
    media_sent_count = 0
    send_error: Exception | None = None
    probably_delivered = False

    upload_render = _upload_renderer(dl_result, plan)
    await board.show(upload_render())
    await board.start_ticking(upload_render)
    send_started = time.monotonic()

    # C-1 шаг 2: внутри try остаются ТОЛЬКО вызовы отправки в Telegram.
    # Записи в БД и ответы пользователю вынесены наружу: раньше транзакция
    # успеха лежала внутри, и её падение откатывало инкремент и success-лог, а
    # управление уходило в except, который писал status="failed" и показывал
    # «Ошибка при отправке», хотя медиа уже было доставлено.
    try:
        if dl_result.file_paths and len(dl_result.file_paths) > 1:
            # Карусель — media group чанками по MEDIA_GROUP_CHUNK_SIZE.
            # Вне альбома идут: анимации (Telegram не смешивает их с фото и
            # видео в одной группе) и изображения тяжелее 10 МБ (их не примут
            # как photo).
            caption = _media_caption(platform, "album")
            total = len(dl_result.file_paths)
            first_media_captioned = False
            for chunk_start in range(0, total, MEDIA_GROUP_CHUNK_SIZE):
                chunk = dl_result.file_paths[chunk_start:chunk_start + MEDIA_GROUP_CHUNK_SIZE]
                sendable: list[tuple[str, str]] = []
                standalone: list[tuple[str, str]] = []
                for path_str in chunk:
                    kind = _classify(path_str)
                    try:
                        size_mb = os.path.getsize(path_str) / (1024 * 1024)
                    except OSError:
                        size_mb = 0
                    if kind == "animation":
                        standalone.append((path_str, "animation"))
                        continue
                    if kind == "image" and size_mb > 10:
                        standalone.append((path_str, "document"))
                        continue
                    sendable.append((path_str, kind))

                if len(sendable) == 1:
                    # Telegram отклоняет альбом не из 2–10 элементов.
                    path_str, kind = sendable[0]
                    f = FSInputFile(path_str)
                    cap = caption if not first_media_captioned else None
                    if kind == "image":
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_photo(photo=ff, caption=c)
                        )
                    else:
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_video(video=ff, caption=c)
                        )
                    first_media_captioned = True
                    media_sent_count += 1
                elif len(sendable) >= 2:
                    media_group = []
                    for path_str, kind in sendable:
                        f = FSInputFile(path_str)
                        cap = caption if not first_media_captioned else None
                        if kind == "image":
                            media_group.append(InputMediaPhoto(media=f, caption=cap))
                        else:
                            media_group.append(InputMediaVideo(media=f, caption=cap))
                        first_media_captioned = True
                    await _send_with_retry(
                        lambda mg=media_group: message.reply_media_group(media=mg)
                    )
                    media_sent_count += len(media_group)

                for path_str, kind in standalone:
                    f = FSInputFile(path_str)
                    cap = caption if not first_media_captioned else None
                    if kind == "animation":
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_animation(animation=ff, caption=c)
                        )
                    else:
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_document(document=ff, caption=c)
                        )
                    first_media_captioned = True
                    media_sent_count += 1
        else:
            media = FSInputFile(dl_result.file_path)
            if dl_result.media_type == "animation":
                await _send_with_retry(
                    lambda: message.reply_animation(
                        animation=media, caption=_media_caption(platform, "animation")
                    )
                )
            elif dl_result.media_type == "image":
                if dl_result.file_size_mb and dl_result.file_size_mb > 10:
                    await _send_with_retry(
                        lambda: message.reply_document(
                            document=media, caption=_media_caption(platform, "image")
                        )
                    )
                else:
                    await _send_with_retry(
                        lambda: message.reply_photo(
                            photo=media, caption=_media_caption(platform, "image")
                        )
                    )
            else:
                # Беззвучное короткое видео (например, Pinterest-пин) —
                # фактически «гифка», и Telegram должен получить её через
                # sendAnimation, а не sendVideo, иначе придёт плеер вместо
                # зацикленного автоплея. ffprobe не должен уметь сломать
                # отправку: любое исключение здесь равнозначно None.
                try:
                    probe_info = await probe_media(Path(dl_result.file_path))
                except Exception as exc:
                    logger.warning(
                        "probe_media упал, отправляем как обычное видео | path={} error={}",
                        dl_result.file_path, exc,
                    )
                    probe_info = None

                if (
                    probe_info is not None
                    and not probe_info.has_audio
                    and 0 < probe_info.duration <= SILENT_VIDEO_AS_ANIMATION_MAX_SEC
                    and Path(dl_result.file_path).suffix.lower() == ".mp4"
                ):
                    await _send_with_retry(
                        lambda: message.reply_animation(
                            animation=media, caption=_media_caption(platform, "animation")
                        )
                    )
                else:
                    video_meta = _video_meta(probe_info)
                    await _send_with_retry(
                        lambda: message.reply_video(
                            video=media,
                            caption=_media_caption(platform, "video"),
                            supports_streaming=True,
                            **video_meta,
                        )
                    )
            media_sent_count = 1
    except ProbablyDeliveredError:
        probably_delivered = True
    except (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError, aiohttp.ClientError) as e:
        send_error = e
        logger.error(
            f"Сетевая ошибка при отправке медиа, попытки исчерпаны | "
            f"sent={media_sent_count}/{media_total_count} error={e}"
        )
    except TelegramForbiddenError as e:
        # Юзер заблокировал бота посреди отправки. Это не сбой: писать ему
        # больше некуда, статус-сообщение править бессмысленно.
        send_error = e
        logger.info("Пользователь заблокировал бота во время отправки | user={}", user_id)
    except Exception as e:
        send_error = e
        logger.error(f"Failed to send file: {e}")
    finally:
        await board.stop_ticking()
        for p in _result_paths(dl_result):
            await remove_file(p)

    if send_error is None and not probably_delivered and _wants_upload_progress(dl_result):
        upload_rate.record(dl_result.file_size_mb, time.monotonic() - send_started)

    if probably_delivered:
        # При multipart у сервера уже своя копия файла (finally выше
        # отработал), поэтому удалять наш экземпляр можно без риска оборвать
        # ещё идущую отдачу.
        await _finish_probably_delivered(status_msg, db_user_id, url, platform, dl_result.file_size_mb)
        return

    if send_error is None:
        # Fix round 2, N2: третий (последний) незащищённый сайт log_download —
        # тот же "database is locked", что уже закрыт на двух других сайтах в
        # Fix round 1. Без guard'а его падение уходит необработанным ДО
        # status_msg.delete()/message.answer(): медиа уже доставлено, единица
        # уже списана (деньги не теряются), но пользователь не видит "Готово",
        # статус-сообщение "Скачиваю..." остаётся висеть навсегда, и в
        # диспетчер летит необработанное исключение.
        await _record_success(db_user_id, url, platform, dl_result.file_size_mb)
        try:
            await status_msg.delete()
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        # Best-effort: медиа доставлено, успех зафиксирован, status_msg удалён.
        # Сбой этого финального ответа не должен ничего переписывать.
        try:
            await message.answer(
                "✅ Готово! Что дальше?",
                reply_markup=get_after_download_kb(is_admin=_is_admin(message.from_user.id)),
            )
        except Exception:
            pass
        return

    # Fix round 1, п.3: тот же порядок, что и в ветке выше — возврат единицы
    # ПЕРЕД записью лога, и сама запись лога обёрнута отдельно.
    if _quota_action(reservation_id is not None, download_ok=True, media_sent_count=media_sent_count) == "refund":
        await _refund_quota(reservation_id)
    try:
        async with async_session() as session, session.begin():
            await log_download(session, db_user_id, url, platform, "failed")
    except Exception as log_exc:
        logger.error(
            "Не удалось записать лог загрузки | user={} error={}", db_user_id, log_exc
        )

    if isinstance(send_error, TelegramEntityTooLarge):
        # Раньше проходил как обычная сетевая ошибка («попробуй ещё раз»),
        # хотя повтор был бы бессмысленным: файл не влезает в лимит.
        try:
            await status_msg.edit_text(TOO_LARGE_FOR_TELEGRAM_TEXT)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        return

    is_network_error = isinstance(
        send_error,
        (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError, aiohttp.ClientError),
    )
    try:
        if media_total_count > 1:
            tail = "ошибка сети" if is_network_error else "ошибка"
            await status_msg.edit_text(
                f"⚠️ Отправлено {media_sent_count} из {media_total_count} файлов, "
                f"дальше произошла {tail}. Попробуй ещё раз."
            )
        elif is_network_error:
            await status_msg.edit_text("⚠️ Ошибка сети при отправке файла. Попробуй ещё раз.")
        else:
            await status_msg.edit_text("⚠️ Ошибка при отправке файла. Попробуй ещё раз.")
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
