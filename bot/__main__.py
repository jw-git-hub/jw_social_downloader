import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from loguru import logger

from bot.config import settings
from bot.db.engine import init_db
from bot.handlers import admin_router, user_router
from bot.middlewares.throttle import ThrottleMiddleware
from bot.services.cleanup import periodic_cleanup
from bot.utils.log_guard import setup_logging


async def main() -> None:
    setup_logging()

    await init_db()

    session = AiohttpSession(timeout=180)
    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.message.middleware(ThrottleMiddleware())

    dp.include_router(admin_router)
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

    _cleanup_task = asyncio.create_task(periodic_cleanup())  # noqa: F841
    _cleanup_task.add_done_callback(_log_task_death)

    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
    ])
    from aiogram.types import BotCommandScopeChat
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Главное меню"),
                BotCommand(command="admin", description="⚙️  Админ-панель"),
            ],
            scope=BotCommandScopeChat(chat_id=settings.ADMIN_ID),
        )
    except Exception:
        pass

    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
