from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
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
from bot.db.queries import (
    get_or_create_user,
    increment_total_downloads,
    log_download,
    refund_free_download,
    reserve_free_download,
)
from bot.keyboards.inline import (
    get_after_download_kb,
    get_back_to_menu_kb,
    get_help_kb,
    get_main_menu_kb,
    get_paywall_kb,
    get_payment_details_kb,
    get_status_kb,
)
from bot.services.cleanup import remove_file
from bot.services.downloader import download_media
from bot.utils.text import esc
from bot.utils.url_parser import parse_url

router = Router(name="user")
download_semaphore = asyncio.Semaphore(3)
waiting_for_url: set[int] = set()

MEDIA_GROUP_CHUNK_SIZE = 5  # Telegram допускает до 10, но большие чанки (~10 МБ) вызывают таймауты
SEND_RETRY_DELAYS = (2, 5, 10)  # экспоненциальный backoff между повторными попытками отправки

def _quota_line(free_left: int, has_subscription: bool) -> str:
    """Одна строка о правах пользователя. Раньше здесь была захардкоженная
    тройка, которую читали и исчерпавший квоту, и платящий подписчик."""
    if has_subscription:
        return "👑 Подписка активна — скачивай без ограничений."
    if free_left > 0:
        return f"🎁 Осталось бесплатных: <b>{free_left} из {settings.FREE_DOWNLOADS}</b>."
    return "🚫 Бесплатные скачивания закончились — нужна подписка."


def _welcome_text(free_left: int, has_subscription: bool) -> str:
    return (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Я — бот для скачивания видео из соцсетей.\n\n"
        "🌐 <b>Поддерживаемые платформы:</b>\n"
        "├ 📸 Instagram\n"
        "├ 🎵 TikTok\n"
        "├ 📘 Facebook\n"
        "├ 📌 Pinterest\n"
        "└ 📺 YouTube\n\n"
        f"{_quota_line(free_left, has_subscription)}\n\n"
        "Выбери действие 👇"
    )


def _help_text(free_left: int, has_subscription: bool) -> str:
    return (
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми <b>«Скачать видео»</b>\n"
        "2️⃣ Отправь ссылку на видео\n"
        "3️⃣ Дождись файла — длинное видео в высоком качестве качается минутами\n\n"
        "🌐 <b>Платформы:</b> Instagram, TikTok, Facebook, Pinterest, YouTube\n\n"
        f"{_quota_line(free_left, has_subscription)}"
    )


def _status_text(
    free_left: int, has_subscription: bool, sub_until, total_downloads: int
) -> str:
    if has_subscription and sub_until is not None:
        sub_text = "✅ до " + sub_until.strftime("%d.%m.%Y")
    else:
        sub_text = "❌ Не активна"
    return (
        f"📊 <b>Твой профиль:</b>\n\n"
        f"🎟 Осталось бесплатных: <b>{free_left} из {settings.FREE_DOWNLOADS}</b>\n"
        f"👑 Подписка: <b>{sub_text}</b>\n"
        f"📥 Всего скачано: <b>{total_downloads}</b>"
    )


def _incoming_text(message) -> str:
    """Текст сообщения или подпись к медиа. Раньше хендлер смотрел только
    в .text, поэтому бот молчал на фото со ссылкой в подписи."""
    return message.text or message.caption or ""


def _is_admin(user_id: int) -> bool:
    return user_id == settings.ADMIN_ID


def _payment_details_text() -> str:
    """Экран реквизитов. Все три значения приходят из .env и экранируются:
    «NGUYEN VAN A & CO» в сыром HTML роняет экран у всех пользователей."""
    return (
        "🏦 <b>Реквизиты для оплаты:</b>\n\n"
        f"💎 <b>USDT (TRC20):</b>\n<code>{esc(settings.USDT_TRC20_ADDRESS)}</code>\n\n"
        f"🇻🇳 <b>VN Bank:</b>\n<code>{esc(settings.VN_BANK_DETAILS)}</code>\n\n"
        f"🇹🇭 <b>TH Bank:</b>\n<code>{esc(settings.TH_BANK_DETAILS)}</code>\n\n"
        "📩 После оплаты отправь скриншот администратору 👇"
    )


def _support_text() -> str:
    return (
        "✉️ <b>Связь с администратором</b>\n\n"
        f"Напиши администратору: {esc(settings.ADMIN_USERNAME)}\n\n"
        "Отправь ему скриншот оплаты или опиши проблему."
    )


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


async def _send_with_retry(send_coro_factory):
    """Выполняет send_coro_factory() с ретраем при временных сетевых сбоях.

    Ретраятся только TelegramNetworkError, TelegramRetryAfter (спим retry_after
    вместо фиксированного backoff), asyncio.TimeoutError и aiohttp.ClientError —
    то есть сетевые/временные проблемы связи с Telegram API. Остальные ошибки
    (например TelegramBadRequest — слишком большой файл, битое медиа) пробрасываются
    сразу, без ретрая, чтобы вызывающий код обработал их как раньше.
    """
    for attempt in range(len(SEND_RETRY_DELAYS) + 1):
        try:
            return await send_coro_factory()
        except TelegramRetryAfter as e:
            if attempt == len(SEND_RETRY_DELAYS):
                raise
            logger.warning(
                f"Telegram просит подождать перед повтором отправки | "
                f"retry_after={e.retry_after}s попытка={attempt + 1}/{len(SEND_RETRY_DELAYS) + 1}"
            )
            await asyncio.sleep(e.retry_after)
        except (TelegramNetworkError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            if attempt == len(SEND_RETRY_DELAYS):
                raise
            delay = SEND_RETRY_DELAYS[attempt]
            logger.warning(
                f"Сетевая ошибка при отправке медиа, повтор через {delay}с | "
                f"попытка={attempt + 1}/{len(SEND_RETRY_DELAYS) + 1} error={e}"
            )
            await asyncio.sleep(delay)


async def _reserve_quota(session, tg_user) -> tuple[int, bool, bool, bool]:
    """Первый шаг загрузки: пользователь, бан, подписка и резерв единицы квоты.

    Возвращает (user_id, is_banned, has_subscription, reserved). Резервирование
    делается ЗДЕСЬ ЖЕ, в той же транзакции, что и чтение — иначе между чтением
    остатка и списанием помещаются параллельные загрузки того же юзера, и с
    одним оставшимся скачиванием он получает три-семь.

    Забаненному и подписчику единица не резервируется: первому загрузка
    запрещена, второму квота не нужна.
    """
    user = await get_or_create_user(session, tg_user)
    db_user_id = user.id
    is_banned = user.is_banned

    sub_until = user.subscription_until
    if sub_until and sub_until.tzinfo is None:
        sub_until = sub_until.replace(tzinfo=timezone.utc)
    has_subscription = bool(sub_until and sub_until > datetime.now(timezone.utc))

    if is_banned or has_subscription:
        return db_user_id, is_banned, has_subscription, False

    reserved = await reserve_free_download(session, db_user_id)
    return db_user_id, is_banned, has_subscription, reserved


async def _user_quota_state(tg_user) -> tuple[int, bool]:
    """(остаток бесплатных, активна ли подписка) — одна короткая транзакция."""
    async with async_session() as session, session.begin():
        user = await get_or_create_user(session, tg_user)
        sub_until = user.subscription_until
        if sub_until and sub_until.tzinfo is None:
            sub_until = sub_until.replace(tzinfo=timezone.utc)
        has_subscription = bool(sub_until and sub_until > datetime.now(timezone.utc))
        return user.free_downloads_left, has_subscription


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


async def _refund_quota(db_user_id: int) -> None:
    """Возврат единицы отдельной короткой транзакцией.

    Падение возврата не должно ронять хендлер: пользователь уже увидел ошибку,
    а потерянная единица — меньшее зло, чем необработанное исключение.
    """
    try:
        async with async_session() as session, session.begin():
            await refund_free_download(session, db_user_id)
    except Exception as exc:
        logger.error("Не удалось вернуть единицу квоты | user={} error={}", db_user_id, exc)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    free_left, has_subscription = await _user_quota_state(message.from_user)
    is_admin = _is_admin(message.from_user.id)
    await message.answer(
        _welcome_text(free_left, has_subscription),
        reply_markup=get_main_menu_kb(is_admin=is_admin),
    )


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    waiting_for_url.discard(callback.from_user.id)
    free_left, has_subscription = await _user_quota_state(callback.from_user)
    is_admin = _is_admin(callback.from_user.id)
    await _safe_edit(
        callback,
        _welcome_text(free_left, has_subscription),
        reply_markup=get_main_menu_kb(is_admin=is_admin),
    )


@router.callback_query(F.data == "menu:download")
async def cb_download(callback: CallbackQuery) -> None:
    await callback.answer()
    waiting_for_url.add(callback.from_user.id)
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
        free_left = user.free_downloads_left
        total_downloads = user.total_downloads
        sub_until = user.subscription_until

    if sub_until and sub_until.tzinfo is None:
        sub_until = sub_until.replace(tzinfo=timezone.utc)
    has_subscription = bool(sub_until and sub_until > datetime.now(timezone.utc))

    await _safe_edit(
        callback,
        _status_text(free_left, has_subscription, sub_until, total_downloads),
        reply_markup=get_status_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.callback_query(F.data == "menu:subscribe")
async def cb_subscribe(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(
        callback,
        "👑 <b>Подписка</b>\n\n"
        "Безлимитное скачивание видео на 30 дней.\n\n"
        "💰 <b>Стоимость:</b>\n"
        f"├ {settings.SUBSCRIPTION_PRICE_USDT} USDT\n"
        f"├ {settings.SUBSCRIPTION_PRICE_VND:,} VND\n"
        f"└ {settings.SUBSCRIPTION_PRICE_THB} THB\n\n"
        "Выбери действие 👇",
        reply_markup=get_paywall_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.callback_query(F.data == "pay:show_details")
async def cb_pay_details(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(
        callback,
        _payment_details_text(),
        reply_markup=get_payment_details_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.callback_query(F.data == "menu:support")
async def cb_support(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(
        callback,
        _support_text(),
        reply_markup=get_back_to_menu_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.answer()
    free_left, has_subscription = await _user_quota_state(callback.from_user)
    await _safe_edit(
        callback,
        _help_text(free_left, has_subscription),
        reply_markup=get_help_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.message(F.text | F.caption)
async def handle_url(message: Message) -> None:
    user_id = message.from_user.id
    result = parse_url(_incoming_text(message))

    if result is None and user_id in waiting_for_url:
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
    waiting_for_url.discard(user_id)

    # C-1 шаг 1: одна короткая транзакция читает пользователя И резервирует
    # единицу квоты. Раньше остаток читался здесь, а списывался после аплоада.
    async with async_session() as session, session.begin():
        db_user_id, is_banned, has_subscription, reserved = await _reserve_quota(
            session, message.from_user
        )

    if is_banned:
        # Резервирования не было — возвращать нечего.
        await message.reply("🚫 Ваш аккаунт заблокирован. Обратитесь к администратору.")
        return

    if not has_subscription and not reserved:
        await message.answer(
            "🚫 Бесплатный лимит исчерпан.\n"
            "Оформи подписку для безлимитного доступа 👇",
            reply_markup=get_paywall_kb(is_admin=_is_admin(message.from_user.id)),
        )
        return

    # Fix round 1, п.3 (окно 1/3): между резервированием и этим вызовом юзер
    # мог заблокировать бота в ту же секунду — раньше единица терялась молча.
    try:
        status_msg = await message.reply(
            "⏳ <b>Скачиваю медиа...</b>\nБольшой файл может качаться несколько минут"
        )
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
        if _quota_action(reserved, download_ok=False, media_sent_count=0) == "refund":
            await _refund_quota(db_user_id)
        return

    # Fix round 1, п.3 (окно 2/3): download_media НЕ гарантирует возврат
    # DownloadResult при любом исходе — до её собственного try (downloader.py)
    # успевают отработать mkdir/gallery-dl fallback/ephemeral cookies, и там
    # может прилететь, например, OSError(ENOSPC) при заполненном диске.
    # Скачивание — БЕЗ открытой db-сессии, чтобы не держать SQLite write-lock
    # на всю длину download+upload.
    try:
        async with download_semaphore:
            dl_result = await download_media(url, platform)
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
        if _quota_action(reserved, download_ok=False, media_sent_count=0) == "refund":
            await _refund_quota(db_user_id)
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
        if _quota_action(reserved, download_ok=False, media_sent_count=0) == "refund":
            await _refund_quota(db_user_id)
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

    media_total_count = len(dl_result.file_paths) if dl_result.file_paths else 1
    media_sent_count = 0
    send_error: Exception | None = None

    # C-1 шаг 2: внутри try остаются ТОЛЬКО вызовы отправки в Telegram.
    # Записи в БД и ответы пользователю вынесены наружу: раньше транзакция
    # успеха лежала внутри, и её падение откатывало инкремент и success-лог, а
    # управление уходило в except, который писал status="failed" и показывал
    # «Ошибка при отправке», хотя медиа уже было доставлено.
    try:
        if dl_result.file_paths and len(dl_result.file_paths) > 1:
            # Множественные файлы (карусель) — отправляем media group чанками.
            # Telegram limit: 10 items per media group, но чанки ближе к 10
            # файлам/10 МБ вызывают таймауты при отправке, поэтому режем
            # на чанки по MEDIA_GROUP_CHUNK_SIZE (5) элементов.
            #
            # F11: Telegram отклоняет фото > 10 МБ и НЕ допускает смешивания
            # документов с фото/видео в одной media group. Поэтому крупные
            # изображения вынимаем из группы и шлём отдельными документами.
            caption = _media_caption(platform, "album")
            total = len(dl_result.file_paths)
            first_media_captioned = False
            for chunk_start in range(0, total, MEDIA_GROUP_CHUNK_SIZE):
                chunk = dl_result.file_paths[chunk_start:chunk_start + MEDIA_GROUP_CHUNK_SIZE]
                sendable: list[tuple[str, bool]] = []
                oversized_images = []
                for path_str in chunk:
                    ext = path_str.rsplit(".", 1)[-1].lower() if "." in path_str else ""
                    is_image = ext in ("jpg", "jpeg", "png", "webp", "heic", "gif")
                    try:
                        size_mb = os.path.getsize(path_str) / (1024 * 1024)
                    except OSError:
                        size_mb = 0
                    if is_image and size_mb > 10:
                        oversized_images.append(path_str)
                        continue
                    sendable.append((path_str, is_image))

                if len(sendable) == 1:
                    # Telegram отклоняет альбом не из 2–10 элементов, поэтому
                    # единственный не-oversized элемент шлём одиночно.
                    path_str, is_image = sendable[0]
                    f = FSInputFile(path_str)
                    cap = caption if not first_media_captioned else None
                    if is_image:
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
                    for path_str, is_image in sendable:
                        f = FSInputFile(path_str)
                        if not first_media_captioned:
                            if is_image:
                                media_group.append(InputMediaPhoto(media=f, caption=caption))
                            else:
                                media_group.append(InputMediaVideo(media=f, caption=caption))
                            first_media_captioned = True
                        else:
                            if is_image:
                                media_group.append(InputMediaPhoto(media=f))
                            else:
                                media_group.append(InputMediaVideo(media=f))
                    await _send_with_retry(lambda mg=media_group: message.reply_media_group(media=mg))
                    media_sent_count += len(media_group)

                for path_str in oversized_images:
                    doc = FSInputFile(path_str)
                    await _send_with_retry(lambda d=doc: message.reply_document(document=d))
                    media_sent_count += 1
        else:
            # Одиночный файл
            media = FSInputFile(dl_result.file_path)
            if dl_result.media_type == "image":
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
                await _send_with_retry(
                    lambda: message.reply_video(
                        video=media, caption=_media_caption(platform, "video")
                    )
                )
            media_sent_count = 1
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
        paths_to_remove = dl_result.file_paths or []
        if dl_result.file_path and dl_result.file_path not in paths_to_remove:
            paths_to_remove.append(dl_result.file_path)
        for p in paths_to_remove:
            await remove_file(p)

    if send_error is None:
        # Fix round 2, N2: третий (последний) незащищённый сайт log_download —
        # тот же "database is locked", что уже закрыт на двух других сайтах в
        # Fix round 1. Без guard'а его падение уходит необработанным ДО
        # status_msg.delete()/message.answer(): медиа уже доставлено, единица
        # уже списана (деньги не теряются), но пользователь не видит "Готово",
        # статус-сообщение "Скачиваю..." остаётся висеть навсегда, и в
        # диспетчер летит необработанное исключение.
        try:
            async with async_session() as session, session.begin():
                await increment_total_downloads(session, db_user_id)
                await log_download(
                    session, db_user_id, url, platform, "success", dl_result.file_size_mb
                )
        except Exception as log_exc:
            logger.error(
                "Не удалось записать успешную загрузку | user={} error={}", db_user_id, log_exc
            )
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
    if _quota_action(reserved, download_ok=True, media_sent_count=media_sent_count) == "refund":
        await _refund_quota(db_user_id)
    try:
        async with async_session() as session, session.begin():
            await log_download(session, db_user_id, url, platform, "failed")
    except Exception as log_exc:
        logger.error(
            "Не удалось записать лог загрузки | user={} error={}", db_user_id, log_exc
        )

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
