from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.db.engine import async_session
from bot.db.queries import get_stats, get_user_by_id, get_user_by_username, toggle_ban, update_subscription
from bot.keyboards.inline import get_admin_menu_kb, get_user_card_kb

router = Router(name="admin")
admin_waiting_search: set[int] = set()


def is_admin(user_id: int) -> bool:
    return user_id == settings.ADMIN_ID


def _user_card_text(user) -> str:
    sub = user.subscription_until.strftime("%d.%m.%Y %H:%M") if user.subscription_until else "❌ Нет"
    status = "🔴 Заблокирован" if user.is_banned else "🟢 Активен"
    return (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📛 Username: @{user.username or 'нет'}\n"
        f"👤 Имя: {user.full_name}\n"
        f"🎟 Бесплатных: {user.free_downloads_left}\n"
        f"👑 Подписка до: {sub}\n"
        f"📥 Скачано: {user.total_downloads}\n"
        f"📌 Статус: {status}"
    )


def _stats_text(stats: dict) -> str:
    return (
        f"📈 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"👑 Активных подписок: <b>{stats['active_subscriptions']}</b>\n"
        f"📥 Скачиваний за 24ч: <b>{stats['downloads_24h']}</b>\n"
        f"📥 Скачиваний за 7д: <b>{stats['downloads_7d']}</b>\n"
        f"📥 Скачиваний за 30д: <b>{stats['downloads_30d']}</b>"
    )


def _stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:stats")],
        [InlineKeyboardButton(text="⚙️  Админ-панель", callback_data="admin:panel")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ])


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


# ── Command handlers ──


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⚙️ <b>Админ-панель</b>", reply_markup=get_admin_menu_kb())


# ── Admin text input (search) ──


class AdminSearchFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        return (
            message.from_user.id == settings.ADMIN_ID
            and message.from_user.id in admin_waiting_search
        )


@router.message(F.text, AdminSearchFilter())
async def admin_text_handler(message: Message):
    admin_waiting_search.discard(message.from_user.id)

    arg = message.text.strip()
    async with async_session() as session, session.begin():
        if arg.startswith("@"):
            user = await get_user_by_username(session, arg.lstrip("@"))
        else:
            try:
                user = await get_user_by_id(session, int(arg))
            except ValueError:
                await message.answer(
                    "Неверный формат. Укажите ID (число) или @username.",
                    reply_markup=get_admin_menu_kb(),
                )
                return

    if not user:
        await message.answer("❌ Пользователь не найден.", reply_markup=get_admin_menu_kb())
        return
    await message.answer(_user_card_text(user), reply_markup=get_user_card_kb(user.id))


# ── Callback handlers ──


@router.callback_query(F.data == "admin:panel")
async def cb_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await _safe_edit(callback, "⚙️ <b>Админ-панель</b>", reply_markup=get_admin_menu_kb())


@router.callback_query(F.data == "admin:search")
async def cb_search(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    admin_waiting_search.add(callback.from_user.id)
    await _safe_edit(
        callback,
        "🔍 <b>Поиск пользователя</b>\n\nОтправь ID или @username:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:cancel_search")],
            [InlineKeyboardButton(text="⚙️  Админ-панель", callback_data="admin:panel")],
        ]),
    )


@router.callback_query(F.data == "admin:cancel_search")
async def cb_cancel_search(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    admin_waiting_search.discard(callback.from_user.id)
    await _safe_edit(callback, "⚙️  <b>Админ-панель</b>", reply_markup=get_admin_menu_kb())


@router.callback_query(F.data == "admin:stats")
async def cb_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    async with async_session() as session, session.begin():
        stats = await get_stats(session)
    await _safe_edit(callback, _stats_text(stats), reply_markup=_stats_kb())


@router.callback_query(F.data.startswith("admin:grant:"))
async def cb_grant(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    user_id = int(parts[2])
    days = int(parts[3])
    async with async_session() as session, session.begin():
        await update_subscription(session, user_id, days)
        user = await get_user_by_id(session, user_id)
    await callback.answer(f"✅ Подписка +{days} дней")
    if user:
        await _safe_edit(callback, _user_card_text(user), reply_markup=get_user_card_kb(user.id))


@router.callback_query(F.data.startswith("admin:ban:"))
async def cb_ban(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    user_id = int(parts[2])
    async with async_session() as session, session.begin():
        new_status = await toggle_ban(session, user_id)
        user = await get_user_by_id(session, user_id)
    await callback.answer("🔴 Заблокирован" if new_status else "🟢 Разблокирован")
    if user:
        await _safe_edit(callback, _user_card_text(user), reply_markup=get_user_card_kb(user.id))
