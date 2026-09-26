import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from loguru import logger

from bot.config import settings
from bot.db.engine import init_db
from bot.handlers import admin_payments_router, admin_router, info_router, payments_router, user_router
from bot.middlewares.throttle import ThrottleMiddleware
from bot.services.cleanup import EFFECTIVE_CLEANUP_MAX_AGE_MIN, periodic_cleanup
from bot.utils.log_guard import setup_logging

BACKLOG_BATCH = 100

PUBLIC_COMMANDS = [
    BotCommand(command="start", description="🏠 Главное меню"),
    BotCommand(command="terms", description="📄 Условия использования"),
    BotCommand(command="support", description="✉️ Поддержка"),
    BotCommand(command="paysupport", description="💳 Вопросы по оплате"),
]


async def replay_pending_payments(bot, dp) -> None:
    """Очередь, накопившаяся за простой: платежи обработать, остальное выбросить.

    Раньше вызывался `start_polling(drop_pending_updates=True)`, но в aiogram 3
    такого параметра нет — он молча уходит в **kwargs, `delete_webhook` не
    вызывается. Значит очередь простоя на деле переигрывалась целиком: старые
    ссылки качались заново, съедая бесплатные скачивания. Выбрасывает её
    именно этот разбор — запрос с `offset = последний + 1` подтверждает
    выброс остального.
    """
    await bot.delete_webhook(drop_pending_updates=False)
    allowed = dp.resolve_used_update_types()
    offset = None
    while True:
        updates = await bot.get_updates(
            offset=offset, timeout=0, limit=BACKLOG_BATCH, allowed_updates=allowed
        )
        if not updates:
            return
        for update in updates:
            if update.message is not None and update.message.successful_payment is not None:
                await dp.feed_update(bot, update)
        offset = updates[-1].update_id + 1


async def run_polling(dp, bot) -> None:
    """Запуск лонг-поллинга после разбора очереди простоя.

    Разбор не удался (сеть) → исключение наружу, процесс падает, перезапуск
    контейнера (`restart: always` в docker-compose.yml) повторит разбор.
    Запасного «сброса» нет сознательно: он либо переигрывал бы старые ссылки
    (drop_pending_updates всё равно игнорируется aiogram 3), либо выбрасывал
    бы непрочитанные платежи.
    """
    await replay_pending_payments(bot, dp)
    await dp.start_polling(bot)


def _api_server() -> TelegramAPIServer:
    """Сервер Bot API, к которому подключается сессия.

    USE_LOCAL_BOT_API=true — свой telegram-bot-api по TELEGRAM_API_BASE
    (docker-compose.yml), иначе облачный api.telegram.org.
    """
    if settings.USE_LOCAL_BOT_API:
        return TelegramAPIServer.from_base(settings.TELEGRAM_API_BASE, is_local=True)
    return PRODUCTION


def build_session() -> AiohttpSession:
    """HTTP-сессия к Bot API.

    Таймаут берётся из настроек и заведомо больше серверного IDLE_TIMEOUT=500:
    соединение должен закрывать сервер, а не мы. Зашитые 180 секунд не
    покрывали отдачу крупного файла даже теоретически. Сервер — облачный или
    локальный — выбирает _api_server() по флагу USE_LOCAL_BOT_API.
    """
    return AiohttpSession(api=_api_server(), timeout=settings.TELEGRAM_REQUEST_TIMEOUT)


UNHANDLED_ERROR_TEXT = "⚠️ Что-то пошло не так. Попробуй ещё раз."


async def on_unhandled_error(event) -> None:
    """Последний рубеж: исключение, не пойманное хендлером.

    Без него любое исключение = тишина для пользователя и крутилка на
    неотвеченном колбэке до 30 секунд. Ответ пользователю — best-effort:
    он мог заблокировать бота ровно этим исключением, и падать здесь
    во второй раз бессмысленно.
    """
    update = getattr(event, "update", None)
    logger.opt(exception=getattr(event, "exception", None)).error(
        "Unhandled update error | update_id={}", getattr(update, "update_id", None)
    )

    callback = getattr(update, "callback_query", None)
    if callback is not None:
        try:
            await callback.answer(UNHANDLED_ERROR_TEXT, show_alert=False)
        except Exception:
            pass
        return

    message = getattr(update, "message", None)
    if message is not None:
        try:
            await message.answer(UNHANDLED_ERROR_TEXT)
        except Exception:
            pass


async def main() -> None:
    setup_logging()

    await init_db()

    session = build_session()
    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.errors.register(on_unhandled_error)

    # Разные лимиты и раздельное состояние: сообщение — это загрузка,
    # колбэк — навигация по меню. Общий лимит в три секунды сделал бы
    # меню неюзабельным.
    dp.message.middleware(ThrottleMiddleware(rate_limit=3.0, notify=True))
    dp.callback_query.middleware(ThrottleMiddleware(rate_limit=0.7, notify=True))

    dp.include_router(admin_router)
    dp.include_router(admin_payments_router)
    dp.include_router(payments_router)
    dp.include_router(info_router)
    dp.include_router(user_router)

    def _log_task_death(task: asyncio.Task) -> None:
        # Фоновая задача не должна завершаться вообще. Если завершилась —
        # об этом надо узнать из лога, а не по отсутствию уборки.
        if task.cancelled():
            logger.info("Cleanup task cancelled")
            return
        exc = task.exception()
        if exc is not None:
            logger.opt(exception=exc).error("Cleanup task died")
        else:
            logger.error("Cleanup task exited unexpectedly")

    # Явный max_age_minutes, а не умолчание сигнатуры: раньше periodic_cleanup
    # звался вовсе без аргументов, брались умолчания 5/10 минут, и настройка
    # CLEANUP_MAX_AGE_MIN=45 не участвовала вовсе (H-находка ревизии 2026-09-12).
    # EFFECTIVE_CLEANUP_MAX_AGE_MIN, а не «сырой» settings.CLEANUP_MAX_AGE_MIN —
    # он же гарантированно больше DOWNLOAD_TIMEOUT (см. bot/services/cleanup.py).
    _cleanup_task = asyncio.create_task(
        periodic_cleanup(max_age_minutes=EFFECTIVE_CLEANUP_MAX_AGE_MIN)
    )  # noqa: F841
    _cleanup_task.add_done_callback(_log_task_death)

    await bot.set_my_commands(PUBLIC_COMMANDS)
    from aiogram.types import BotCommandScopeChat
    try:
        await bot.set_my_commands(
            [*PUBLIC_COMMANDS, BotCommand(command="admin", description="⚙️  Админ-панель")],
            scope=BotCommandScopeChat(chat_id=settings.ADMIN_ID),
        )
    except Exception:
        pass

    logger.info(
        "Bot API transport | mode={} max_file_mb={}",
        "local" if settings.USE_LOCAL_BOT_API else "cloud",
        settings.MAX_FILE_SIZE_MB,
    )
    logger.info("Bot started")
    await run_polling(dp, bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
