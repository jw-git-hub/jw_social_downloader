"""Команды, которых Telegram требует от бота с оплатой: /terms, /support, /paysupport."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import settings
from bot.utils.text import esc

router = Router(name="info")


def support_text() -> str:
    return (
        "✉️ <b>Связь с администратором</b>\n\n"
        f"Напиши администратору: {esc(settings.ADMIN_USERNAME)}\n\n"
        "Опиши проблему и приложи ссылку, которая не скачалась."
    )


def paysupport_text() -> str:
    return (
        "💳 <b>Оплата и возвраты</b>\n\n"
        f"Напиши администратору: {esc(settings.ADMIN_USERNAME)} — укажи дату платежа "
        "и что случилось.\n\n"
        "Возврат делаем, если бот не смог скачать то, ради чего ты оформил подписку, "
        "и поддержка не помогла. Подробно — /terms."
    )


def terms_text() -> str:
    admin = esc(settings.ADMIN_USERNAME)
    return (
        "📄 <b>Условия использования</b>\n\n"
        "1. <b>Что это.</b> Бот скачивает видео и фото по ссылкам из Instagram, TikTok, "
        "Facebook, Pinterest и YouTube и присылает их в чат.\n\n"
        f"2. <b>Бесплатно.</b> {settings.FREE_DOWNLOADS_PER_DAY} скачивания за любые 24 часа. "
        "Неудачное скачивание не считается.\n\n"
        f"3. <b>Подписка.</b> {settings.SUBSCRIPTION_PRICE_STARS} ⭐ за 30 дней безлимитных "
        "скачиваний, оплата звёздами Telegram. Продлевается автоматически каждые 30 дней, "
        "пока ты её не отменишь: настройки Telegram → «Мои звёзды». После отмены работает "
        "до конца оплаченного срока.\n\n"
        "4. <b>Возврат.</b> Если бот не смог скачать то, ради чего ты оформил подписку, и "
        "поддержка не помогла — напиши в /paysupport и укажи дату платежа. Решение о возврате "
        "принимает администратор. При возврате подписка отключается сразу.\n\n"
        "5. <b>Ограничения.</b> Скачивается только общедоступное: приватные, удалённые и "
        "закрытые в регионе публикации недоступны. Соцсети меняют свои сайты — отдельные "
        "ссылки могут временно не скачиваться. Бесперебойная работа 24/7 не гарантируется.\n\n"
        "6. <b>Контент.</b> Ты сам отвечаешь за использование скачанного: соблюдай авторские "
        "права и правила площадок. Бот не хранит файлы — они удаляются сразу после отправки.\n\n"
        "7. <b>Данные.</b> Храним твой Telegram ID, имя и ник, историю скачиваний (ссылки — "
        "до 180 дней) и платежей. Третьим лицам не передаём.\n\n"
        f"8. <b>Связь.</b> Вопросы — /support, оплата и возвраты — /paysupport или {admin}.\n\n"
        "9. Условия могут меняться; актуальная версия — по команде /terms."
    )


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    await message.answer(support_text())


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    await message.answer(paysupport_text())


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    await message.answer(terms_text())
