from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.text import stars_text


def _maybe_admin_row(is_admin: bool) -> list[list[InlineKeyboardButton]]:
    if is_admin:
        return [[InlineKeyboardButton(text="⚙️  Админ-панель", callback_data="admin:panel")]]
    return []


def get_main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📥 Скачать видео", callback_data="menu:download")],
        [InlineKeyboardButton(text="📊 Мой статус", callback_data="menu:status")],
        [InlineKeyboardButton(text="👑 Подписка", callback_data="menu:subscribe")],
        [InlineKeyboardButton(text="📖 Помощь", callback_data="menu:help")],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="menu:support")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_back_to_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")]]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_paywall_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👑 Оформить подписку", callback_data="menu:subscribe")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_subscribe_kb(invoice_url: str, price: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"⭐ Оформить за {stars_text(price)}", url=invoice_url)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_status_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👑 Оформить подписку", callback_data="menu:subscribe")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_help_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📥 Скачать видео", callback_data="menu:download")],
        [InlineKeyboardButton(text="👑 Подписка", callback_data="menu:subscribe")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_after_download_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📥 Скачать ещё", callback_data="menu:download")],
        [InlineKeyboardButton(text="📊 Мой статус", callback_data="menu:status")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="admin:search")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ])


def get_user_card_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 +7 дней", callback_data=f"admin:grant:{user_id}:7"),
            InlineKeyboardButton(text="📅 +30 дней", callback_data=f"admin:grant:{user_id}:30"),
        ],
        [InlineKeyboardButton(text="💸 Платежи", callback_data=f"admin:payments:{user_id}")],
        [InlineKeyboardButton(text="🔨 Бан/Разбан", callback_data=f"admin:ban:{user_id}")],
        [InlineKeyboardButton(text="🔍 Найти другого", callback_data="admin:search")],
        [InlineKeyboardButton(text="⚙️  Админ-панель", callback_data="admin:panel")],
    ])


def get_payments_kb(user_id: int, payments) -> InlineKeyboardMarkup:
    """Кнопка возврата — только у невозвращённых платежей."""
    rows = [
        [InlineKeyboardButton(
            text=f"↩️ Вернуть {p.amount} ⭐ от {p.created_at:%d.%m}",
            callback_data=f"admin:refund_ask:{p.id}",
        )]
        for p in payments
        if p.refunded_at is None
    ]
    rows.append([InlineKeyboardButton(text="👤 Карточка", callback_data=f"admin:card:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_refund_confirm_kb(payment_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Вернуть", callback_data=f"admin:refund_do:{payment_id}")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data=f"admin:payments:{user_id}")],
    ])
