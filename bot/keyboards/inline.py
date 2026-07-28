from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _maybe_admin_row(is_admin: bool) -> list[list[InlineKeyboardButton]]:
    if is_admin:
        return [[InlineKeyboardButton(text="⚙️  Админ-панель", callback_data="admin:panel")]]
    return []


def get_main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📥 Скачать видео", callback_data="menu:download")],
        [InlineKeyboardButton(text="📊 Мой статус", callback_data="menu:status")],
        [InlineKeyboardButton(text="👑 Подписка", callback_data="menu:subscribe")],
        [
            InlineKeyboardButton(text="📖 Помощь", callback_data="menu:help"),
            InlineKeyboardButton(text="✉️  Поддержка", callback_data="menu:support"),
        ],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_back_to_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")]]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_paywall_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💳 Показать реквизиты", callback_data="pay:show_details")],
        [InlineKeyboardButton(text="✉️  Связаться с админом", callback_data="menu:support")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
    ]
    rows.extend(_maybe_admin_row(is_admin))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_payment_details_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✉️  Отправить скриншот админу", callback_data="menu:support")],
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
        [InlineKeyboardButton(text="🔨 Бан/Разбан", callback_data=f"admin:ban:{user_id}")],
        [InlineKeyboardButton(text="🔍 Найти другого", callback_data="admin:search")],
        [InlineKeyboardButton(text="⚙️  Админ-панель", callback_data="admin:panel")],
    ])
