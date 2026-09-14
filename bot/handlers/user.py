from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, InputMediaVideo, Message
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
from bot.utils.url_parser import parse_url

router = Router(name="user")
download_semaphore = asyncio.Semaphore(3)
waiting_for_url: set[int] = set()

MEDIA_GROUP_CHUNK_SIZE = 5  # Telegram допускает до 10, но большие чанки (~10 МБ) вызывают таймауты
SEND_RETRY_DELAYS = (2, 5, 10)  # экспоненциальный backoff между повторными попытками отправки

WELCOME_TEXT = (
    "👋 <b>Добро пожаловать!</b>\n\n"
    "Я — бот для скачивания видео из соцсетей.\n\n"
    "🌐 <b>Поддерживаемые платформы:</b>\n"
    "├ 📸 Instagram\n"
    "├ 🎵 TikTok\n"
    "├ 📘 Facebook\n"
    "├ 📌 Pinterest\n"
    "└ 📺 YouTube\n\n"
    "🎁 У тебя <b>3 бесплатных</b> скачивания.\n\n"
    "Выбери действие 👇"
)


def _is_admin(user_id: int) -> bool:
    return user_id == settings.ADMIN_ID


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


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
    async with async_session() as session, session.begin():
        await get_or_create_user(session, message.from_user)
    is_admin = _is_admin(message.from_user.id)
    await message.answer(WELCOME_TEXT, reply_markup=get_main_menu_kb(is_admin=is_admin))


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    waiting_for_url.discard(callback.from_user.id)
    is_admin = _is_admin(callback.from_user.id)
    await _safe_edit(callback, WELCOME_TEXT, reply_markup=get_main_menu_kb(is_admin=is_admin))


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

    sub_until = user.subscription_until
    if sub_until and sub_until.tzinfo is None:
        sub_until = sub_until.replace(tzinfo=timezone.utc)
    if sub_until and sub_until > datetime.now(timezone.utc):
        sub_text = "✅ до " + sub_until.strftime("%d.%m.%Y")
    else:
        sub_text = "❌ Не активна"

    await _safe_edit(
        callback,
        f"📊 <b>Твой профиль:</b>\n\n"
        f"🎟 Бесплатных скачиваний: <b>{user.free_downloads_left}/{settings.FREE_DOWNLOADS}</b>\n"
        f"👑 Подписка: <b>{sub_text}</b>\n"
        f"📥 Всего скачано: <b>{user.total_downloads}</b>",
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
        "🏦 <b>Реквизиты для оплаты:</b>\n\n"
        f"💎 <b>USDT (TRC20):</b>\n<code>{settings.USDT_TRC20_ADDRESS}</code>\n\n"
        f"🇻🇳 <b>VN Bank:</b>\n<code>{settings.VN_BANK_DETAILS}</code>\n\n"
        f"🇹🇭 <b>TH Bank:</b>\n<code>{settings.TH_BANK_DETAILS}</code>\n\n"
        "📩 После оплаты отправь скриншот администратору 👇",
        reply_markup=get_payment_details_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.callback_query(F.data == "menu:support")
async def cb_support(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(
        callback,
        "✉️ <b>Связь с администратором</b>\n\n"
        f"Напиши администратору: {settings.ADMIN_USERNAME}\n\n"
        "Отправь ему скриншот оплаты или опиши проблему.",
        reply_markup=get_back_to_menu_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(
        callback,
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми <b>«Скачать видео»</b>\n"
        "2️⃣ Отправь ссылку на видео\n"
        "3️⃣ Получи видео за секунды!\n\n"
        "🌐 <b>Платформы:</b> Instagram, TikTok, Facebook, Pinterest, YouTube\n\n"
        "🎁 <b>3 бесплатных</b> скачивания, далее — подписка.",
        reply_markup=get_help_kb(is_admin=_is_admin(callback.from_user.id)),
    )


@router.message(F.text)
async def handle_url(message: Message) -> None:
    user_id = message.from_user.id
    result = parse_url(message.text)

    if result is None and user_id in waiting_for_url:
        await message.answer(
            "🔗 Это не похоже на ссылку. Отправь ссылку из Instagram, TikTok, Facebook, Pinterest или YouTube.",
            reply_markup=get_back_to_menu_kb(is_admin=_is_admin(message.from_user.id)),
        )
        return

    if result is None:
        await message.answer("Выбери действие 👇", reply_markup=get_main_menu_kb(is_admin=_is_admin(message.from_user.id)))
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

    status_msg = await message.reply("⏳ <b>Скачиваю медиа...</b>\nЭто займёт несколько секунд")

    # Скачивание — БЕЗ открытой db-сессии, чтобы не держать SQLite write-lock
    # на всю длину download+upload.
    async with download_semaphore:
        dl_result = await download_media(url, platform)

    if not dl_result.success:
        async with async_session() as session, session.begin():
            await log_download(session, db_user_id, url, platform, "failed")
        if _quota_action(reserved, download_ok=False, media_sent_count=0) == "refund":
            await _refund_quota(db_user_id)
        try:
            await status_msg.edit_text(
                f"❌ <b>Не удалось скачать</b>\n{dl_result.error_message}"
            )
        except TelegramBadRequest:
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
            caption = f"✅ Медиа из {platform.capitalize()}"
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
                            document=media, caption=f"✅ Фото из {platform.capitalize()}"
                        )
                    )
                else:
                    await _send_with_retry(
                        lambda: message.reply_photo(
                            photo=media, caption=f"✅ Фото из {platform.capitalize()}"
                        )
                    )
            else:
                await _send_with_retry(
                    lambda: message.reply_video(
                        video=media, caption=f"✅ Видео из {platform.capitalize()}"
                    )
                )
            media_sent_count = 1
    except (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError, aiohttp.ClientError) as e:
        send_error = e
        logger.error(
            f"Сетевая ошибка при отправке медиа, попытки исчерпаны | "
            f"sent={media_sent_count}/{media_total_count} error={e}"
        )
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
        async with async_session() as session, session.begin():
            await increment_total_downloads(session, db_user_id)
            await log_download(
                session, db_user_id, url, platform, "success", dl_result.file_size_mb
            )
        try:
            await status_msg.delete()
        except TelegramBadRequest:
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

    async with async_session() as session, session.begin():
        await log_download(session, db_user_id, url, platform, "failed")
    if _quota_action(reserved, download_ok=True, media_sent_count=media_sent_count) == "refund":
        await _refund_quota(db_user_id)

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
    except TelegramBadRequest:
        pass
