import asyncio
from datetime import timezone
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from admin_audit import log_event
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    FSInputFile,
    WebAppInfo,
    InputMediaPhoto,
)

from channels import check_sub
from config import CHANNELS, WEBAPP_URL
from database import (
    add_referral,
    add_user,
    get_next_referral_level,
    get_referral_rank,
    get_top_referrers,
    get_total_discount,
    get_user,
    get_vip_until,
)
from referral import get_level, progress_bar
from free_signals import (
    FREE_SIGNALS_LIMIT,
    get_remaining_free_signals,
)
from subscriptions import (
    build_plan_description,
    calculate_discounted_price,
    get_all_plans,
    get_plan,
)

from signals import (
    get_signal_statistics,
    get_user_signal_history,
    get_user_signal_statistics,
)
from user_preferences import (
    get_preferences,
    set_language,
    toggle_preference,
)

router = Router()


async def animate_loading(
    message: Message,
    steps: list[tuple[str, int]],
    delay: float = 0.35,
) -> Message:
    """Показывает компактную анимацию в одном сообщении."""
    loading_message = await message.answer(
        "⏳ Подготовка..."
    )

    total_blocks = 10

    for title, percent in steps:
        filled = max(
            0,
            min(
                total_blocks,
                round(percent / 100 * total_blocks),
            ),
        )
        progress = "█" * filled + "░" * (
            total_blocks - filled
        )

        try:
            await loading_message.edit_text(
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{title}\n\n"
                f"{progress} {percent}%\n\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
        except Exception:
            pass

        await asyncio.sleep(delay)

    return loading_message


async def animate_callback_loading(
    callback: CallbackQuery,
    steps: list[tuple[str, int]],
    delay: float = 0.25,
) -> None:
    """Анимация перед открытием callback-раздела."""
    total_blocks = 10

    for title, percent in steps:
        filled = max(
            0,
            min(
                total_blocks,
                round(percent / 100 * total_blocks),
            ),
        )
        progress = "█" * filled + "░" * (
            total_blocks - filled
        )

        try:
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=(
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{title}\n\n"
                        f"{progress} {percent}%\n\n"
                        "━━━━━━━━━━━━━━━━━━━━"
                    ),
                    reply_markup=None,
                )
            else:
                await callback.message.edit_text(
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{title}\n\n"
                    f"{progress} {percent}%\n\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                )
        except Exception:
            pass

        await asyncio.sleep(delay)

MANAGER_USERNAME = "liquidityplusmanager"
BOT_USERNAME = "LiquidityPlus_Bot"
MAIN_BANNER_PATH = "images/main_banner.png"

BANNER_PATHS = {
    "home": "images/main_banner.png",
    "signals": "images/signals.png",
    "profile": "images/profile.png",
    "vip": "images/vip.png",
    "referrals": "images/referrals.png",
    "statistics": "images/statistics.png",
    "settings": "images/settings.png",
    "support": "images/support.png",
    "payment": "images/payment.png",
    "about": "images/about.png",
}




TEXTS = {
    "ru": {
        "home_title": "LIQUIDITY PLUS TERMINAL",
        "user": "Пользователь",
        "level": "Уровень",
        "vip": "VIP",
        "system": "СИСТЕМА",
        "scanner": "AI Scanner",
        "order_flow": "Order Flow",
        "monitor": "Signal Monitor",
        "account": "АККАУНТ",
        "signal_access": "Доступ к сигналам",
        "partners": "Партнёров",
        "discount": "Персональная скидка",
        "choose": "Выберите раздел:",
        "active": "Активен",
        "inactive": "Не активен",
        "unlimited": "Без ограничений",
        "profile": "ЛИЧНЫЙ КАБИНЕТ",
        "subscription": "ПОДПИСКА",
        "plan": "Тариф",
        "until": "Активен до",
        "balance": "Баланс",
        "possibilities": "ВОЗМОЖНОСТИ",
        "free_signals": "Бесплатные сигналы",
        "invited": "Приглашено",
        "statistics": "СТАТИСТИКА",
        "received": "Получено сигналов",
        "wins": "Прибыльных",
        "losses": "Убыточных",
        "be": "Безубыток",
        "active_signals": "Активных",
        "winrate": "Win Rate",
        "result": "Суммарный результат",
        "history": "ИСТОРИЯ СИГНАЛОВ",
        "no_history": "История пока пуста.",
        "settings": "НАСТРОЙКИ",
        "language": "Язык",
        "notifications": "УВЕДОМЛЕНИЯ",
        "new_signals": "Новые сигналы",
        "tp_updates": "Достижение TP",
        "sl_updates": "Stop Loss",
        "ai_ideas": "AI-идеи",
        "daily": "Ежедневная аналитика",
        "enabled": "ВКЛ",
        "disabled": "ВЫКЛ",
        "language_changed": "Язык изменён.",
    },
    "uk": {
        "home_title": "LIQUIDITY PLUS TERMINAL",
        "user": "Користувач",
        "level": "Рівень",
        "vip": "VIP",
        "system": "СИСТЕМА",
        "scanner": "AI Scanner",
        "order_flow": "Order Flow",
        "monitor": "Signal Monitor",
        "account": "АККАУНТ",
        "signal_access": "Доступ до сигналів",
        "partners": "Партнерів",
        "discount": "Персональна знижка",
        "choose": "Оберіть розділ:",
        "active": "Активний",
        "inactive": "Не активний",
        "unlimited": "Без обмежень",
        "profile": "ОСОБИСТИЙ КАБІНЕТ",
        "subscription": "ПІДПИСКА",
        "plan": "Тариф",
        "until": "Активний до",
        "balance": "Баланс",
        "possibilities": "МОЖЛИВОСТІ",
        "free_signals": "Безкоштовні сигнали",
        "invited": "Запрошено",
        "statistics": "СТАТИСТИКА",
        "received": "Отримано сигналів",
        "wins": "Прибуткових",
        "losses": "Збиткових",
        "be": "Беззбиток",
        "active_signals": "Активних",
        "winrate": "Win Rate",
        "result": "Сумарний результат",
        "history": "ІСТОРІЯ СИГНАЛІВ",
        "no_history": "Історія поки порожня.",
        "settings": "НАЛАШТУВАННЯ",
        "language": "Мова",
        "notifications": "СПОВІЩЕННЯ",
        "new_signals": "Нові сигнали",
        "tp_updates": "Досягнення TP",
        "sl_updates": "Stop Loss",
        "ai_ideas": "AI-ідеї",
        "daily": "Щоденна аналітика",
        "enabled": "УВІМК",
        "disabled": "ВИМК",
        "language_changed": "Мову змінено.",
    },
    "en": {
        "home_title": "LIQUIDITY PLUS TERMINAL",
        "user": "User",
        "level": "Level",
        "vip": "VIP",
        "system": "SYSTEM",
        "scanner": "AI Scanner",
        "order_flow": "Order Flow",
        "monitor": "Signal Monitor",
        "account": "ACCOUNT",
        "signal_access": "Signal access",
        "partners": "Partners",
        "discount": "Personal discount",
        "choose": "Choose a section:",
        "active": "Active",
        "inactive": "Inactive",
        "unlimited": "Unlimited",
        "profile": "PERSONAL ACCOUNT",
        "subscription": "SUBSCRIPTION",
        "plan": "Plan",
        "until": "Active until",
        "balance": "Balance",
        "possibilities": "FEATURES",
        "free_signals": "Free signals",
        "invited": "Invited",
        "statistics": "STATISTICS",
        "received": "Signals received",
        "wins": "Wins",
        "losses": "Losses",
        "be": "Breakeven",
        "active_signals": "Active",
        "winrate": "Win Rate",
        "result": "Total result",
        "history": "SIGNAL HISTORY",
        "no_history": "History is empty.",
        "settings": "SETTINGS",
        "language": "Language",
        "notifications": "NOTIFICATIONS",
        "new_signals": "New signals",
        "tp_updates": "TP updates",
        "sl_updates": "Stop Loss",
        "ai_ideas": "AI ideas",
        "daily": "Daily analytics",
        "enabled": "ON",
        "disabled": "OFF",
        "language_changed": "Language changed.",
    },
}


def user_language(user_id: int) -> str:
    return get_preferences(user_id).language


def tr(user_id: int, key: str) -> str:
    language = user_language(user_id)
    return TEXTS.get(language, TEXTS["ru"]).get(
        key,
        TEXTS["ru"].get(key, key),
    )


def on_off(value: bool, user_id: int) -> str:
    return tr(user_id, "enabled") if value else tr(user_id, "disabled")


def settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    prefs = get_preferences(user_id)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🇷🇺 RU",
                    callback_data="lang:ru",
                ),
                InlineKeyboardButton(
                    text="🇺🇦 UA",
                    callback_data="lang:uk",
                ),
                InlineKeyboardButton(
                    text="🇬🇧 EN",
                    callback_data="lang:en",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"📈 {tr(user_id, 'new_signals')}: {on_off(prefs.new_signals, user_id)}",
                    callback_data="pref:new_signals",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🎯 {tr(user_id, 'tp_updates')}: {on_off(prefs.tp_updates, user_id)}",
                    callback_data="pref:tp_updates",
                ),
                InlineKeyboardButton(
                    text=f"🛑 {tr(user_id, 'sl_updates')}: {on_off(prefs.sl_updates, user_id)}",
                    callback_data="pref:sl_updates",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"🤖 {tr(user_id, 'ai_ideas')}: {on_off(prefs.ai_ideas, user_id)}",
                    callback_data="pref:ai_ideas",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📊 {tr(user_id, 'daily')}: {on_off(prefs.daily_analytics, user_id)}",
                    callback_data="pref:daily_analytics",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="menu_home",
                )
            ],
        ]
    )


def statistics_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 История сигналов",
                    callback_data="menu_history",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="menu_home",
                )
            ],
        ]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Открыть Liquidity App",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📈 Сигналы",
                    callback_data="menu_signals",
                ),
                InlineKeyboardButton(
                    text="👤 Кабинет",
                    callback_data="menu_profile",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💎 Premium",
                    callback_data="menu_vip",
                ),
                InlineKeyboardButton(
                    text="👥 Партнёры",
                    callback_data="menu_referrals",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚡ Trading Hub",
                    callback_data="menu_market_hub",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Аналитика",
                    callback_data="menu_statistics",
                ),
                InlineKeyboardButton(
                    text="⚙️ Настройки",
                    callback_data="menu_settings",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🛟 Поддержка",
                    callback_data="menu_support",
                ),
                InlineKeyboardButton(
                    text="ℹ️ О сервисе",
                    callback_data="menu_about",
                ),
            ],
        ]
    )


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛟 Написать менеджеру",
                    url=f"https://t.me/{MANAGER_USERNAME}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Открыть общий чат",
                    url="https://t.me/liquiditypluschat",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="menu_home",
                )
            ],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="menu_home",
                )
            ]
        ]
    )


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 Купить VIP",
                    callback_data="menu_vip",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Моя реферальная ссылка",
                    callback_data="menu_referrals",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="menu_home",
                )
            ],
        ]
    )



async def render_callback(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    parse_mode: str | None = None,
    banner_key: str | None = None,
) -> None:
    """Безопасно обновляет текст и баннер текущего экрана."""
    banner_path = BANNER_PATHS.get(banner_key or "")

    try:
        if callback.message.photo:
            if banner_path and Path(banner_path).is_file():
                await callback.message.edit_media(
                    media=InputMediaPhoto(
                        media=FSInputFile(banner_path),
                        caption=text,
                        parse_mode=parse_mode,
                    ),
                    reply_markup=reply_markup,
                )
            else:
                await callback.message.edit_caption(
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
        else:
            if banner_path and Path(banner_path).is_file():
                await callback.message.answer_photo(
                    photo=FSInputFile(banner_path),
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
            else:
                await callback.message.edit_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
    except Exception as error:
        if "message is not modified" in str(error).lower():
            return

        if banner_path and Path(banner_path).is_file():
            await callback.message.answer_photo(
                photo=FSInputFile(banner_path),
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        else:
            await callback.message.answer(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )

def subscribe_keyboard() -> InlineKeyboardMarkup:
    buttons = []

    for channel in CHANNELS:
        username = channel.replace("@", "")

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"📢 {channel}",
                    url=f"https://t.me/{username}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="✅ Проверить подписку",
                callback_data="check_sub",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vip_plans_keyboard(
    user_id: int,
) -> InlineKeyboardMarkup:
    discount = get_total_discount(user_id)
    buttons = []

    for plan in get_all_plans():
        final_price = calculate_discounted_price(
            plan.code,
            discount,
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"💎 {plan.title} — "
                        f"${final_price:.2f}"
                    ),
                    callback_data=f"vip_plan:{plan.code}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="menu_home",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(
    plan_code: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Перейти к оплате",
                    callback_data=f"pay_plan:{plan_code}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛟 Написать менеджеру",
                    url=f"https://t.me/{MANAGER_USERNAME}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к тарифам",
                    callback_data="vip_plans",
                )
            ],
        ]
    )


async def require_subscription(
    message: Message,
) -> bool:
    subscribed = await check_sub(
        message.bot,
        message.from_user.id,
    )

    if subscribed:
        return True

    await message.answer(
        "Для использования бота подпишитесь "
        "на все обязательные каналы.",
        reply_markup=subscribe_keyboard(),
    )

    return False


async def require_subscription_callback(
    callback: CallbackQuery,
) -> bool:
    subscribed = await check_sub(
        callback.bot,
        callback.from_user.id,
    )

    if subscribed:
        return True

    await render_callback(
        callback,
        "Для использования бота подпишитесь "
        "на все обязательные каналы.",
        reply_markup=subscribe_keyboard(),
    )

    await callback.answer(
        "Сначала подпишитесь на каналы.",
        show_alert=True,
    )

    return False


def format_vip_until(user_id: int) -> str:
    vip_until = get_vip_until(user_id)

    if vip_until is None:
        return "Без ограничения срока"

    local_time = vip_until.astimezone(timezone.utc)

    return local_time.strftime("%d.%m.%Y %H:%M UTC")


def get_user_status_text(user_id: int) -> tuple[str, str, int, int]:
    user = get_user(user_id)

    if not user:
        return "Не активен", "—", 0, FREE_SIGNALS_LIMIT

    vip_status = bool(user[4])
    vip_text = "Активен" if vip_status else "Не активен"
    vip_until_text = (
        format_vip_until(user_id)
        if vip_status
        else "—"
    )

    remaining = get_remaining_free_signals(user_id)

    return (
        vip_text,
        vip_until_text,
        int(user[5] or 0),
        remaining,
    )


def build_home_text(
    user_id: int,
    first_name: str | None,
) -> str:
    vip_text, _, referrals, remaining = get_user_status_text(user_id)
    discount = get_total_discount(user_id)
    stats = get_signal_statistics()

    vip_active = vip_text == "Активен"
    localized_vip = tr(user_id, "active") if vip_active else tr(user_id, "inactive")
    level = "PREMIUM" if vip_active else "STANDARD"
    access = tr(user_id, "unlimited") if vip_active else f"{remaining}/{FREE_SIGNALS_LIMIT}"

    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"  {tr(user_id, 'home_title')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 {tr(user_id, 'user')}: {first_name or '—'}\n"
        f"◈ {tr(user_id, 'level')}: {level}\n"
        f"💎 {tr(user_id, 'vip')}: {localized_vip}\n\n"
        f"{tr(user_id, 'system')}\n"
        f"🤖 {tr(user_id, 'scanner')}: ONLINE\n"
        f"📡 {tr(user_id, 'order_flow')}: ONLINE\n"
        f"⚡ {tr(user_id, 'monitor')}: ACTIVE\n\n"
        f"{tr(user_id, 'account')}\n"
        f"📈 {tr(user_id, 'signal_access')}: {access}\n"
        f"📊 {tr(user_id, 'active_signals')}: {stats['active']}\n"
        f"🏆 {tr(user_id, 'winrate')}: {stats['winrate']:.1f}%\n"
        f"👥 {tr(user_id, 'partners')}: {referrals}\n"
        f"🏷 {tr(user_id, 'discount')}: {discount}%\n\n"
        f"{tr(user_id, 'choose')}"
    )


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
):
    user = message.from_user

    add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    referral_added = False

    if command.args:
        try:
            referrer_id = int(command.args.strip())

            referral_added = add_referral(
                user_id=user.id,
                referrer_id=referrer_id,
            )

        except (TypeError, ValueError):
            referral_added = False

    if not await require_subscription(message):
        return

    loading_message = await animate_loading(
        message,
        [
            ("🔌 Подключение к LiquidityPlus...", 20),
            ("📊 Загрузка рыночных модулей...", 45),
            ("🤖 Проверка AI Scanner...", 70),
            ("✅ Терминал готов", 100),
        ],
        delay=0.30,
    )

    try:
        await loading_message.delete()
    except Exception:
        pass

    text = build_home_text(
        user_id=user.id,
        first_name=user.first_name,
    )

    if referral_added:
        text += (
            "\n\n✅ Вы зарегистрированы "
            "по реферальной ссылке."
        )

    try:
        cleanup_message = await message.answer(
            "Меню обновлено.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await cleanup_message.delete()
    except Exception:
        pass

    try:
        if Path(MAIN_BANNER_PATH).is_file():
            await message.answer_photo(
                photo=FSInputFile(MAIN_BANNER_PATH),
                caption=text,
                reply_markup=main_menu_keyboard(),
            )
        else:
            raise FileNotFoundError(
                f"Баннер не найден: {MAIN_BANNER_PATH}"
            )
    except Exception as error:
        print(
            f"Не удалось отправить главный баннер: {error}"
        )

        await message.answer(
            text,
            reply_markup=main_menu_keyboard(),
        )


@router.callback_query(F.data == "menu_home")
async def show_home(callback: CallbackQuery):
    if not await require_subscription_callback(callback):
        return

    await render_callback(
        callback,
        build_home_text(
            user_id=callback.from_user.id,
            first_name=callback.from_user.first_name,
        ),
        reply_markup=main_menu_keyboard(),
        banner_key="home",
    )

    await callback.answer()


@router.callback_query(F.data == "menu_signals")
async def show_signals(callback: CallbackQuery):
    if not await require_subscription_callback(callback):
        return

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала отправьте /start.",
            show_alert=True,
        )
        return

    vip_status = bool(user[4])
    remaining = get_remaining_free_signals(
        callback.from_user.id
    )

    if vip_status:
        text = (
            "╭━━━━━━━━━━━━━━━━━━╮\n"
            "   ТОРГОВЫЕ СИГНАЛЫ\n"
            "╰━━━━━━━━━━━━━━━━━━╯\n\n"
            "Ваш VIP-доступ активен.\n\n"
            "Вы получаете все сигналы без ограничений, "
            "включая сопровождение TP и Stop Loss.\n\n"
            "Новые сигналы приходят автоматически."
        )
    else:
        text = (
            "╭━━━━━━━━━━━━━━━━━━╮\n"
            "   ТОРГОВЫЕ СИГНАЛЫ\n"
            "╰━━━━━━━━━━━━━━━━━━╯\n\n"
            f"Доступно бесплатных сигналов: "
            f"{remaining}/{FREE_SIGNALS_LIMIT}\n\n"
            "После окончания лимита потребуется VIP.\n\n"
            "Новые доступные сигналы приходят автоматически."
        )

    await render_callback(
        callback,
        text,
        banner_key="signals",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💎 Открыть VIP",
                        callback_data="menu_vip",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Главное меню",
                        callback_data="menu_home",
                    )
                ],
            ]
        ),
    )

    await callback.answer()


@router.callback_query(F.data == "menu_profile")
async def show_profile(callback: CallbackQuery):
    await animate_callback_loading(
        callback,
        [
            ("📂 Загрузка профиля...", 30),
            ("💎 Проверка подписки...", 65),
            ("📊 Загрузка статистики...", 100),
        ],
    )
    if not await require_subscription_callback(callback):
        return

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала отправьте /start.",
            show_alert=True,
        )
        return

    username = (
        f"@{user[1]}"
        if user[1]
        else "не указан"
    )

    first_name = user[2] or "не указано"
    balance = float(user[3] or 0)
    vip_status = bool(user[4])
    referrals = int(user[5] or 0)
    subscription_plan = user[9] or "нет"
    discount = get_total_discount(
        callback.from_user.id
    )
    remaining = get_remaining_free_signals(
        callback.from_user.id
    )

    vip_text = (
        "Активен"
        if vip_status
        else "Не активен"
    )

    vip_until_text = (
        format_vip_until(callback.from_user.id)
        if vip_status
        else "—"
    )

    account_level = "VIP" if vip_status else "STANDARD"

    account_level = (
        "PREMIUM"
        if vip_status
        else "STANDARD"
    )

    stats = get_user_signal_statistics(callback.from_user.id)

    await render_callback(
        callback,
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"      {tr(callback.from_user.id, 'profile')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 {first_name}\n"
        f"🔗 {username}\n"
        f"🆔 ID: {user[0]}\n"
        f"◈ {tr(callback.from_user.id, 'level')}: {account_level}\n\n"
        f"{tr(callback.from_user.id, 'subscription')}\n"
        f"💎 VIP: {vip_text}\n"
        f"📦 {tr(callback.from_user.id, 'plan')}: {subscription_plan}\n"
        f"📅 {tr(callback.from_user.id, 'until')}: {vip_until_text}\n"
        f"💰 {tr(callback.from_user.id, 'balance')}: ${balance:.2f}\n\n"
        f"{tr(callback.from_user.id, 'statistics')}\n"
        f"📨 {tr(callback.from_user.id, 'received')}: {stats['total']}\n"
        f"✅ {tr(callback.from_user.id, 'wins')}: {stats['wins']}\n"
        f"❌ {tr(callback.from_user.id, 'losses')}: {stats['losses']}\n"
        f"🛡 {tr(callback.from_user.id, 'be')}: {stats['breakeven']}\n"
        f"🏆 {tr(callback.from_user.id, 'winrate')}: {stats['winrate']:.1f}%\n"
        f"📈 {tr(callback.from_user.id, 'result')}: {stats['total_result']:+.2f}%\n\n"
        f"👥 {tr(callback.from_user.id, 'invited')}: {referrals}\n"
        f"🏷 {tr(callback.from_user.id, 'discount')}: {discount}%\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=profile_keyboard(),
        banner_key="profile",
    )

    await callback.answer()


@router.callback_query(F.data == "menu_vip")
async def show_vip(callback: CallbackQuery):
    await animate_callback_loading(
        callback,
        [
            ("💎 Проверка VIP-статуса...", 35),
            ("🏷 Расчёт скидки...", 70),
            ("✅ Тарифы загружены", 100),
        ],
    )
    if not await require_subscription_callback(callback):
        return

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала отправьте /start.",
            show_alert=True,
        )
        return

    vip_status = bool(user[4])
    discount = get_total_discount(
        callback.from_user.id
    )

    status_text = (
        "Ваш VIP-статус активен."
        if vip_status
        else "VIP пока не активирован."
    )

    extra_text = ""

    if vip_status:
        extra_text = (
            f"\nТариф: {user[9] or 'Без срока'}\n"
            f"VIP до: "
            f"{format_vip_until(callback.from_user.id)}\n"
        )

    await render_callback(
        callback,
        "╭━━━━━━━━━━━━━━━━━━╮\n"
        "      VIP-ДОСТУП\n"
        "╰━━━━━━━━━━━━━━━━━━╯\n\n"
        f"{status_text}\n"
        f"{extra_text}\n"
        "◈ Все торговые сигналы\n"
        "◈ AI Scanner и Smart Money-фильтры\n"
        "◈ Live Order Flow и CVD\n"
        "◈ Автоматическое сопровождение TP/SL\n"
        "◈ Приоритетная аналитика\n"
        "◈ Без месячного лимита\n\n"
        f"Ваша скидка: {discount}%\n\n"
        "Выберите тариф:",
        reply_markup=vip_plans_keyboard(
            callback.from_user.id
        ),
        banner_key="vip",
    )

    await callback.answer()


@router.callback_query(F.data == "vip_plans")
async def show_vip_plans(
    callback: CallbackQuery,
):
    discount = get_total_discount(
        callback.from_user.id
    )

    await render_callback(
        callback,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💎 ВЫБЕРИТЕ ТАРИФ\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Ваша скидка: {discount}%",
        reply_markup=vip_plans_keyboard(
            callback.from_user.id
        ),
        banner_key="vip",
    )

    await callback.answer()


@router.callback_query(
    F.data.startswith("vip_plan:")
)
async def select_vip_plan(
    callback: CallbackQuery,
):
    plan_code = callback.data.split(
        ":",
        maxsplit=1,
    )[1]

    plan = get_plan(plan_code)

    if plan is None:
        await callback.answer(
            "Тариф не найден.",
            show_alert=True,
        )
        return

    discount = get_total_discount(
        callback.from_user.id
    )

    description = build_plan_description(
        plan_code,
        discount,
    )

    await render_callback(
        callback,
        description,
        reply_markup=payment_keyboard(plan_code),
        banner_key="vip",
    )

    await callback.answer()


@router.callback_query(F.data == "menu_referrals")
async def show_referrals(callback: CallbackQuery):
    if not await require_subscription_callback(callback):
        return

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала отправьте /start.",
            show_alert=True,
        )
        return

    referral_count = int(user[5] or 0)
    vip_status = bool(user[4])
    discount = get_total_discount(
        callback.from_user.id
    )

    referral_link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={callback.from_user.id}"
    )

    next_level = get_next_referral_level(
        referral_count
    )

    if next_level:
        required_referrals, next_discount = next_level
        remaining = (
            required_referrals - referral_count
        )

        next_level_text = (
            f"Следующая скидка: {next_discount}%\n"
            f"Осталось пригласить: {remaining}"
        )
    else:
        next_level_text = (
            "Максимальный реферальный уровень достигнут."
        )

    vip_bonus = (
        "Активен: +5%"
        if vip_status
        else "Не активен"
    )
    level = get_level(referral_count)
    rank = get_referral_rank(callback.from_user.id)
    level_progress = progress_bar(referral_count)

    await render_callback(
        callback,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👥 РЕФЕРАЛЬНАЯ СИСТЕМА\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Уровень: {level.title}\n"
        f"Место в рейтинге: #{rank}\n"
        f"Прогресс: {level_progress}\n\n"
        f"Приглашено: {referral_count}\n"
        f"Итоговая скидка: {discount}%\n"
        f"VIP-бонус: {vip_bonus}\n\n"
        f"{next_level_text}\n\n"
        "Ваша персональная ссылка:\n"
        f"{referral_link}\n\n"
        "После 300 приглашённых VIP "
        "активируется автоматически.\n"
        "Максимальная итоговая скидка — 55%.",
        banner_key="referrals",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📤 Поделиться ссылкой",
                        url=(
                            "https://t.me/share/url"
                            f"?url={referral_link}"
                            "&text=LiquidityPlus"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏆 Рейтинг партнёров",
                        callback_data="referral_leaderboard",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Главное меню",
                        callback_data="menu_home",
                    )
                ],
            ]
        ),
    )

    await callback.answer()


@router.callback_query(F.data == "menu_statistics")
async def show_statistics(callback: CallbackQuery):
    await animate_callback_loading(
        callback,
        [
            ("📊 Loading statistics...", 35),
            ("📈 Calculating results...", 70),
            ("✅ Ready", 100),
        ],
    )
    if not await require_subscription_callback(callback):
        return

    stats = get_user_signal_statistics(callback.from_user.id)

    await render_callback(
        callback,
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"       {tr(callback.from_user.id, 'statistics')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📨 {tr(callback.from_user.id, 'received')}: {stats['total']}\n"
        f"📡 {tr(callback.from_user.id, 'active_signals')}: {stats['active']}\n"
        f"✅ {tr(callback.from_user.id, 'wins')}: {stats['wins']}\n"
        f"❌ {tr(callback.from_user.id, 'losses')}: {stats['losses']}\n"
        f"🛡 {tr(callback.from_user.id, 'be')}: {stats['breakeven']}\n"
        f"🏆 {tr(callback.from_user.id, 'winrate')}: {stats['winrate']:.1f}%\n"
        f"📈 {tr(callback.from_user.id, 'result')}: {stats['total_result']:+.2f}%\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=statistics_keyboard(),
        banner_key="statistics",
    )

    await callback.answer()


@router.callback_query(F.data == "menu_history")
async def show_history(callback: CallbackQuery):
    if not await require_subscription_callback(callback):
        return

    history = get_user_signal_history(
        callback.from_user.id,
        limit=10,
    )

    if not history:
        text = (
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"      {tr(callback.from_user.id, 'history')}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{tr(callback.from_user.id, 'no_history')}"
        )
    else:
        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            f"      {tr(callback.from_user.id, 'history')}",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        for item in history:
            direction_icon = "🟢" if item["direction"] == "LONG" else "🔴"
            status_map = {
                "active": "ACTIVE",
                "win": "WIN",
                "loss": "LOSS",
                "breakeven": "BE",
            }
            status = status_map.get(item["status"], item["status"].upper())
            result = item["result_percent"]
            result_text = "—" if result is None else f"{float(result):+.2f}%"

            lines.append(
                f"#{item['signal_id']} {direction_icon} {item['symbol']} | "
                f"{status} | {result_text}"
            )

        text = "\n".join(lines)

    await render_callback(
        callback,
        text,
        reply_markup=statistics_keyboard(),
        banner_key="statistics",
    )
    await callback.answer()



@router.callback_query(F.data == "referral_leaderboard")
async def referral_leaderboard(callback: CallbackQuery):
    top = get_top_referrers(10)
    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        "🏆 РЕЙТИНГ ПАРТНЁРОВ",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    medals = ["🥇", "🥈", "🥉"]
    for index, user in enumerate(top, start=1):
        name = f"@{user[1]}" if user[1] else (user[2] or str(user[0]))
        prefix = medals[index - 1] if index <= 3 else f"{index}."
        lines.append(f"{prefix} {name} — {int(user[3] or 0)}")
    if not top:
        lines.append("Пока никто не пригласил пользователей.")
    await render_callback(
        callback,
        "\n".join(lines),
        banner_key="referrals",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_referrals")
        ]]),
    )
    await callback.answer()

@router.callback_query(F.data == "menu_settings")
async def show_settings(callback: CallbackQuery):
    await animate_callback_loading(
        callback,
        [
            ("⚙️ Loading settings...", 50),
            ("✅ Ready", 100),
        ],
    )
    if not await require_subscription_callback(callback):
        return

    prefs = get_preferences(callback.from_user.id)
    language_name = {
        "ru": "Русский",
        "uk": "Українська",
        "en": "English",
    }.get(prefs.language, "Русский")

    await render_callback(
        callback,
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"       {tr(callback.from_user.id, 'settings')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 {tr(callback.from_user.id, 'language')}: {language_name}\n\n"
        f"{tr(callback.from_user.id, 'notifications')}\n"
        f"📈 {tr(callback.from_user.id, 'new_signals')}: {on_off(prefs.new_signals, callback.from_user.id)}\n"
        f"🎯 {tr(callback.from_user.id, 'tp_updates')}: {on_off(prefs.tp_updates, callback.from_user.id)}\n"
        f"🛑 {tr(callback.from_user.id, 'sl_updates')}: {on_off(prefs.sl_updates, callback.from_user.id)}\n"
        f"🤖 {tr(callback.from_user.id, 'ai_ideas')}: {on_off(prefs.ai_ideas, callback.from_user.id)}\n"
        f"📊 {tr(callback.from_user.id, 'daily')}: {on_off(prefs.daily_analytics, callback.from_user.id)}\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=settings_keyboard(callback.from_user.id),
        banner_key="settings",
    )

    await callback.answer()


@router.callback_query(F.data.startswith("lang:"))
async def change_language(callback: CallbackQuery):
    language = callback.data.split(":", 1)[1]
    set_language(callback.from_user.id, language)
    await callback.answer(
        tr(callback.from_user.id, "language_changed"),
        show_alert=True,
    )
    await show_settings(callback)


@router.callback_query(F.data.startswith("pref:"))
async def change_preference(callback: CallbackQuery):
    field_name = callback.data.split(":", 1)[1]
    toggle_preference(callback.from_user.id, field_name)
    await callback.answer()
    await show_settings(callback)


@router.callback_query(F.data == "menu_support")
async def show_support(callback: CallbackQuery):
    if not await require_subscription_callback(callback):
        return

    await render_callback(
        callback,
        "╭━━━━━━━━━━━━━━━━━━╮\n"
        "       ПОДДЕРЖКА 24/7\n"
        "╰━━━━━━━━━━━━━━━━━━╯\n\n"
        "Центр поддержки LiquidityPlus.\n\n"
        "◈ Оплата и активация VIP\n"
        "◈ Работа сигналов и уведомлений\n"
        "◈ Восстановление доступа\n"
        "◈ Технические вопросы\n\n"
        "Среднее время ответа: 5–15 минут.\n\n"
        "Выберите способ связи:",
        reply_markup=support_keyboard(),
        banner_key="support",
    )

    await callback.answer()


@router.callback_query(F.data == "menu_about")
async def show_about(callback: CallbackQuery):
    if not await require_subscription_callback(callback):
        return

    await render_callback(
        callback,
        "╭━━━━━━━━━━━━━━━━━━╮\n"
        "   О LIQUIDITY PLUS\n"
        "╰━━━━━━━━━━━━━━━━━━╯\n\n"
        "LiquidityPlus — бот для получения "
        "торговых сигналов и анализа криптовалютного рынка.\n\n"
        "В системе используются:\n"
        "• AI Market Scanner\n"
        "• Funding и Open Interest\n"
        "• RSI, EMA и объёмы\n"
        "• Order Flow и CVD\n"
        "• Автоматическое сопровождение TP/SL\n\n"
        "Сигналы не гарантируют прибыль. "
        "Всегда соблюдайте риск-менеджмент.",
        reply_markup=back_to_menu_keyboard(),
        banner_key="about",
    )

    await callback.answer()


# Поддержка старых кнопок у пользователей,
# у которых ещё осталась Reply-клавиатура.
@router.message(F.text == "📈 Сигналы")
async def legacy_signals(message: Message):
    await message.answer(
        "Откройте новое меню:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "👤 Профиль")
async def legacy_profile(message: Message):
    await message.answer(
        "Откройте новое меню:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "💎 VIP")
async def legacy_vip(message: Message):
    await message.answer(
        "Откройте новое меню:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "👥 Рефералы")
async def legacy_referrals(message: Message):
    await message.answer(
        "Откройте новое меню:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "⚙️ Поддержка")
async def legacy_support(message: Message):
    await message.answer(
        "Нажмите кнопку ниже, чтобы открыть чат менеджера:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛟 Открыть поддержку",
                        url=f"https://t.me/{MANAGER_USERNAME}",
                    )
                ]
            ]
        ),
    )

@router.message(F.text == "/app")
async def open_webapp_command(message: Message):
    await message.answer(
        "Откройте LiquidityPlus WebApp:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚀 Открыть приложение",
                        web_app=WebAppInfo(url=WEBAPP_URL),
                    )
                ]
            ]
        ),
    )
