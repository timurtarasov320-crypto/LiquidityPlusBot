import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import ADMIN_ID
from admin_audit import create_audit_table, get_recent_events, log_event
from database import (
    activate_subscription,
    get_all_users,
    get_project_growth_stats,
    get_top_referrers,
    get_user,
    search_users,
    set_blocked,
    set_vip,

    create_trading_signal,
    add_signal_event,
    close_trading_signal,
    get_active_trading_signals,
    get_signal_statistics,
    get_trading_signal,
)

from signals import (
    create_signal,
    format_signal,
    get_signal,
    send_signal_to_users,
    signal_admin_keyboard,

    update_signal_field,
    get_signal_recipient_ids,)

router = Router()


async def animate_admin_action(
    message: Message,
    steps: list[tuple[str, int]],
    delay: float = 0.25,
) -> Message:
    progress_message = await message.answer(
        "⏳ Подготовка..."
    )

    for title, percent in steps:
        filled = round(percent / 10)
        bar = "█" * filled + "░" * (10 - filled)

        try:
            await progress_message.edit_text(
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{title}\n\n"
                f"{bar} {percent}%\n\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
        except Exception:
            pass

        await asyncio.sleep(delay)

    return progress_message


class SignalEditStates(StatesGroup):
    waiting_for_value = State()


class AdminStates(StatesGroup):
    waiting_vip_user = State()
    waiting_vip_days = State()
    waiting_unvip_user = State()
    waiting_search = State()
    waiting_broadcast = State()
    waiting_signal = State()
    waiting_block_user = State()
    waiting_unblock_user = State()


class SignalBuilderStates(StatesGroup):
    symbol = State()
    direction = State()
    entry = State()
    stop_loss = State()
    take_profit_1 = State()
    take_profit_2 = State()
    take_profit_3 = State()
    risk = State()
    comment = State()
    score = State()
    audience = State()
    confirm = State()


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
                InlineKeyboardButton(text="👥 Аудитория", callback_data="admin:users"),
            ],
            [
                InlineKeyboardButton(text="💳 Оплаты", callback_data="admin:payments"),
                InlineKeyboardButton(text="🏆 Рефералы", callback_data="admin:referrals"),
            ],
            [
                InlineKeyboardButton(text="🔎 Найти пользователя", callback_data="admin:search"),
            ],
            [
                InlineKeyboardButton(text="💎 Выдать VIP", callback_data="admin:vip"),
                InlineKeyboardButton(text="❌ Забрать VIP", callback_data="admin:unvip"),
            ],
            [
                InlineKeyboardButton(text="🚫 Заблокировать", callback_data="admin:block"),
                InlineKeyboardButton(text="✅ Разблокировать", callback_data="admin:unblock"),
            ],
            [
                InlineKeyboardButton(text="📨 Broadcast", callback_data="admin:broadcast"),
                InlineKeyboardButton(text="📈 Новый сигнал", callback_data="admin:signal_builder"),
            ],
            [
                InlineKeyboardButton(text="📡 Signal Control", callback_data="admin:tracking"),
                InlineKeyboardButton(text="🧾 Журнал", callback_data="admin:audit"),
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить данные", callback_data="admin:home"),
                InlineKeyboardButton(text="✖️ Закрыть", callback_data="admin:close"),
            ],
        ]
    )


def direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 LONG", callback_data="signal_builder:direction:LONG"),
                InlineKeyboardButton(text="🔴 SHORT", callback_data="signal_builder:direction:SHORT"),
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin:cancel")],
        ]
    )


def skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="signal_builder:skip")],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin:cancel")],
        ]
    )


def audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Всем", callback_data="signal_builder:audience:all")],
            [
                InlineKeyboardButton(text="💎 Только VIP", callback_data="signal_builder:audience:vip"),
                InlineKeyboardButton(text="🎁 Только FREE", callback_data="signal_builder:audience:free"),
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin:cancel")],
        ]
    )


def signal_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Создать и отправить", callback_data="signal_builder:send")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:cancel")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin:cancel")]
        ]
    )


def user_action_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 Выдать VIP",
                    callback_data=f"admin:quick_vip:{user_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Забрать VIP",
                    callback_data=f"admin:quick_unvip:{user_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Заблокировать",
                    callback_data=f"admin:quick_block:{user_id}",
                ),
                InlineKeyboardButton(
                    text="✅ Разблокировать",
                    callback_data=f"admin:quick_unblock:{user_id}",
                ),
            ],
            [InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="admin:home")],
        ]
    )


def payment_stats() -> tuple[int, float, int]:
    try:
        conn = sqlite3.connect("payments.db")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END), 0)
            FROM payments
            """
        )
        total, revenue, paid = cursor.fetchone()
        conn.close()
        return int(total or 0), float(revenue or 0), int(paid or 0)
    except sqlite3.Error:
        return 0, 0.0, 0


def format_user(user: tuple) -> str:
    username = f"@{user[1]}" if user[1] else "не указан"
    first_name = user[2] or "не указано"
    vip = "активен" if user[4] else "не активен"
    blocked = "да" if len(user) > 10 and user[10] else "нет"
    return (
        "👤 ПОЛЬЗОВАТЕЛЬ\n\n"
        f"ID: {user[0]}\n"
        f"Имя: {first_name}\n"
        f"Username: {username}\n"
        f"VIP: {vip}\n"
        f"Тариф: {user[9] or 'нет'}\n"
        f"Рефералы: {int(user[5] or 0)}\n"
        f"Баланс: ${float(user[3] or 0):.2f}\n"
        f"Заблокирован: {blocked}\n"
        f"Регистрация: {user[7] or 'неизвестно'}"
    )


async def ensure_admin_message(message: Message) -> bool:
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return False
    return True


async def ensure_admin_callback(callback: CallbackQuery) -> bool:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return False
    return True


async def show_admin_home(target: Message | CallbackQuery) -> None:
    create_audit_table()
    users = get_all_users()
    vip_count = sum(1 for user in users if user[4])
    blocked_count = sum(1 for user in users if len(user) > 10 and user[10])
    _, revenue, paid = payment_stats()
    text = (
        "⚙️ АДМИН-ПАНЕЛЬ LIQUIDITY PLUS\n"
        "Версия: Signal Tracking\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"💎 Активных VIP: {vip_count}\n"
        f"🚫 Заблокировано: {blocked_count}\n"
        f"💳 Оплаченных счетов: {paid}\n"
        f"💰 Выручка: ${revenue:.2f}\n\n"
        "Выберите действие:"
    )
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=admin_keyboard())
        except Exception:
            await target.message.answer(text, reply_markup=admin_keyboard())
        await target.answer()
    else:
        await target.answer(text, reply_markup=admin_keyboard())


@router.message(Command("id"))
async def show_id(message: Message):
    await message.answer(
        f"Твой Telegram ID: {message.from_user.id}\n"
        f"ADMIN_ID в config.py: {ADMIN_ID}"
    )


@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not await ensure_admin_message(message):
        return
    await state.clear()
    await show_admin_home(message)


@router.callback_query(F.data == "admin:home")
async def admin_home_callback(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await show_admin_home(callback)


@router.callback_query(F.data.in_({"admin:cancel", "admin:close"}))
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    if callback.data == "admin:close":
        await callback.message.delete()
        await callback.answer()
    else:
        await show_admin_home(callback)


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    growth = get_project_growth_stats()
    invoices, revenue, paid = payment_stats()
    conversion = (paid / invoices * 100) if invoices else 0
    vip_share = (growth["vip"] / growth["total"] * 100) if growth["total"] else 0
    text = (
        "📊 АНАЛИТИКА LIQUIDITY PLUS\n\n"
        f"👥 Всего: {growth['total']}\n"
        f"🆕 За 24 часа: +{growth['day']}\n"
        f"📅 За 7 дней: +{growth['week']}\n"
        f"🗓 За 30 дней: +{growth['month']}\n\n"
        f"💎 Активных VIP: {growth['vip']} ({vip_share:.1f}%)\n"
        f"🚫 Заблокировано: {growth['blocked']}\n"
        f"🔗 Приглашений: {growth['referrals']}\n\n"
        f"🧾 Счетов: {invoices}\n"
        f"✅ Оплачено: {paid}\n"
        f"📈 Конверсия оплаты: {conversion:.1f}%\n"
        f"💰 Выручка: ${revenue:.2f}"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:home")]]
    ))
    await callback.answer()


@router.callback_query(F.data == "admin:payments")
async def admin_payments(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    try:
        conn = sqlite3.connect("payments.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT invoice_id, user_id, plan_code, amount, asset, status, created_at "
            "FROM payments ORDER BY created_at DESC LIMIT 12"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        rows = []
    lines = ["💳 ПОСЛЕДНИЕ ОПЛАТЫ", ""]
    for row in rows:
        icon = "✅" if row["status"] == "paid" else "⏳" if row["status"] == "active" else "❌"
        lines.append(f"{icon} #{row['invoice_id']} | {row['user_id']} | {row['plan_code']} | {row['amount']:.2f} {row['asset']}")
    if not rows:
        lines.append("Платежей пока нет.")
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:home")]]
    ))
    await callback.answer()


@router.callback_query(F.data == "admin:referrals")
async def admin_referrals(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    top = get_top_referrers(15)
    lines = ["🏆 ТОП ПАРТНЁРОВ", ""]
    for index, user in enumerate(top, 1):
        name = f"@{user[1]}" if user[1] else (user[2] or str(user[0]))
        lines.append(f"{index}. {name} | ID {user[0]} | {int(user[3] or 0)}")
    if not top:
        lines.append("Данных пока нет.")
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:home")]]
    ))
    await callback.answer()




@router.callback_query(F.data == "admin:audit")
async def admin_audit_log(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    rows = get_recent_events(20)
    labels = {
        "vip_granted": "VIP выдан",
        "vip_removed": "VIP снят",
        "user_blocked": "пользователь заблокирован",
        "user_unblocked": "пользователь разблокирован",
        "broadcast_sent": "рассылка отправлена",
        "signal_sent": "сигнал опубликован",
        "payment_activated": "оплата активирована",
        "referral_added": "реферал засчитан",
    }
    lines = ["🧾 ЖУРНАЛ ДЕЙСТВИЙ", ""]
    for row in rows:
        stamp = str(row["created_at"] or "").replace("T", " ")[:16]
        action = labels.get(row["action"], row["action"])
        target = f" → {row['target_id']}" if row["target_id"] else ""
        lines.append(f"• {stamp} | {action}{target}")
    if not rows:
        lines.append("Событий пока нет.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:audit")], [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:home")]]),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    users = get_all_users()
    if not users:
        text = "В базе пока нет пользователей."
    else:
        lines = ["👥 ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ\n"]
        for user in users[:25]:
            username = f"@{user[1]}" if user[1] else "без username"
            flags = []
            if user[4]:
                flags.append("VIP")
            if len(user) > 10 and user[10]:
                flags.append("BLOCK")
            status = ", ".join(flags) or "FREE"
            lines.append(f"{user[0]} | {username} | {status}")
        text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Найти", callback_data="admin:search")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:home")],
        ]
    ))
    await callback.answer()


@router.callback_query(F.data == "admin:search")
async def start_search(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.set_state(AdminStates.waiting_search)
    await callback.message.edit_text(
        "Введите Telegram ID, username или имя пользователя:",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_search)
async def process_search(message: Message, state: FSMContext):
    if not await ensure_admin_message(message):
        return
    query = message.text.strip()
    users = search_users(query, limit=10)
    await state.clear()
    if not users:
        await message.answer("Пользователь не найден.", reply_markup=admin_keyboard())
        return
    for user in users:
        await message.answer(format_user(user), reply_markup=user_action_keyboard(user[0]))


@router.callback_query(F.data == "admin:vip")
async def start_vip(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.set_state(AdminStates.waiting_vip_user)
    await callback.message.edit_text("Введите Telegram ID пользователя:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AdminStates.waiting_vip_user)
async def vip_user_entered(message: Message, state: FSMContext):
    if not await ensure_admin_message(message):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен числовой Telegram ID.")
        return
    if not get_user(user_id):
        await message.answer("Пользователь не найден в базе.")
        return
    await state.update_data(user_id=user_id)
    await state.set_state(AdminStates.waiting_vip_days)
    await message.answer(
        "Введите срок VIP в днях.\n"
        "Например: 30, 90, 365.\n"
        "Введите 0 для бессрочного VIP.",
        reply_markup=cancel_keyboard(),
    )


@router.message(AdminStates.waiting_vip_days)
async def vip_days_entered(message: Message, state: FSMContext):
    if not await ensure_admin_message(message):
        return
    try:
        days = int(message.text.strip())
        if days < 0 or days > 3650:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 0 до 3650.")
        return
    data = await state.get_data()
    user_id = int(data["user_id"])
    if days == 0:
        ok = set_vip(user_id, 1)
        label = "бессрочно"
        if ok:
            log_event("vip_granted", message.from_user.id, user_id, days=0)
    else:
        until = datetime.now(timezone.utc) + timedelta(days=days)
        ok = activate_subscription(user_id, f"manual_{days}d", until)
        label = f"на {days} дней"
        if ok:
            log_event("vip_granted", message.from_user.id, user_id, days=days)
    await state.clear()
    await message.answer(
        f"✅ VIP выдан пользователю {user_id} {label}." if ok else "Не удалось обновить пользователя.",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:unvip")
async def start_unvip(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.set_state(AdminStates.waiting_unvip_user)
    await callback.message.edit_text("Введите Telegram ID пользователя:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AdminStates.waiting_unvip_user)
async def unvip_user_entered(message: Message, state: FSMContext):
    if not await ensure_admin_message(message):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен числовой Telegram ID.")
        return
    ok = set_vip(user_id, 0)
    await state.clear()
    await message.answer(
        f"✅ VIP снят у пользователя {user_id}." if ok else "Пользователь не найден.",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await callback.message.edit_text(
        "Отправьте сообщение для рассылки всем пользователям.\n"
        "Текст, фото или документ будут скопированы как есть.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


async def copy_to_users(message: Message, only_vip: bool = False) -> tuple[int, int]:
    users = get_all_users()
    sent = 0
    failed = 0
    for user in users:
        if len(user) > 10 and user[10]:
            continue
        if only_vip and not user[4]:
            continue
        try:
            await message.bot.copy_message(
                chat_id=user[0],
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)
    return sent, failed


@router.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if not await ensure_admin_message(message):
        return
    await state.clear()
    status = await message.answer("⏳ Рассылка запущена...")
    sent, failed = await copy_to_users(message, only_vip=False)
    await status.edit_text(
        f"✅ Рассылка завершена.\n\nДоставлено: {sent}\nОшибок: {failed}",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:signal_builder")
async def start_signal_builder(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(SignalBuilderStates.symbol)
    await callback.message.edit_text(
        "Введите монету, например BTC или BTC/USDT:",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(SignalBuilderStates.symbol)
async def signal_symbol(message: Message, state: FSMContext):
    symbol = (message.text or "").strip().upper().replace("-", "/")
    if not symbol:
        await message.answer("Введите название монеты.")
        return
    if "/" not in symbol:
        symbol = f"{symbol}/USDT"
    await state.update_data(symbol=symbol)
    await state.set_state(SignalBuilderStates.direction)
    await message.answer("Выберите направление:", reply_markup=direction_keyboard())


@router.callback_query(SignalBuilderStates.direction, F.data.startswith("signal_builder:direction:"))
async def signal_direction(callback: CallbackQuery, state: FSMContext):
    direction = callback.data.rsplit(":", 1)[1]
    await state.update_data(direction=direction)
    await state.set_state(SignalBuilderStates.entry)
    await callback.message.edit_text("Введите зону входа, например 65000–65500:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(SignalBuilderStates.entry)
async def signal_entry(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value:
        await message.answer("Введите зону входа.")
        return
    await state.update_data(entry=value)
    await state.set_state(SignalBuilderStates.stop_loss)
    await message.answer("Введите Stop Loss:", reply_markup=cancel_keyboard())


@router.message(SignalBuilderStates.stop_loss)
async def signal_stop(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value:
        await message.answer("Введите Stop Loss.")
        return
    await state.update_data(stop_loss=value)
    await state.set_state(SignalBuilderStates.take_profit_1)
    await message.answer("Введите TP1:", reply_markup=cancel_keyboard())


@router.message(SignalBuilderStates.take_profit_1)
async def signal_tp1(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value:
        await message.answer("TP1 обязателен.")
        return
    await state.update_data(take_profit_1=value)
    await state.set_state(SignalBuilderStates.take_profit_2)
    await message.answer("Введите TP2 или нажмите «Пропустить»:", reply_markup=skip_keyboard())


async def move_optional_signal_step(state: FSMContext, field: str, value: str | None, next_state: State) -> None:
    await state.update_data(**{field: value})
    await state.set_state(next_state)


@router.message(SignalBuilderStates.take_profit_2)
async def signal_tp2(message: Message, state: FSMContext):
    await move_optional_signal_step(state, "take_profit_2", (message.text or "").strip() or None, SignalBuilderStates.take_profit_3)
    await message.answer("Введите TP3 или нажмите «Пропустить»:", reply_markup=skip_keyboard())


@router.callback_query(SignalBuilderStates.take_profit_2, F.data == "signal_builder:skip")
async def skip_tp2(callback: CallbackQuery, state: FSMContext):
    await move_optional_signal_step(state, "take_profit_2", None, SignalBuilderStates.take_profit_3)
    await callback.message.edit_text("Введите TP3 или нажмите «Пропустить»:", reply_markup=skip_keyboard())
    await callback.answer()


@router.message(SignalBuilderStates.take_profit_3)
async def signal_tp3(message: Message, state: FSMContext):
    await move_optional_signal_step(state, "take_profit_3", (message.text or "").strip() or None, SignalBuilderStates.risk)
    await message.answer("Введите риск, например 1%, или пропустите:", reply_markup=skip_keyboard())


@router.callback_query(SignalBuilderStates.take_profit_3, F.data == "signal_builder:skip")
async def skip_tp3(callback: CallbackQuery, state: FSMContext):
    await move_optional_signal_step(state, "take_profit_3", None, SignalBuilderStates.risk)
    await callback.message.edit_text("Введите риск, например 1%, или пропустите:", reply_markup=skip_keyboard())
    await callback.answer()


@router.message(SignalBuilderStates.risk)
async def signal_risk(message: Message, state: FSMContext):
    await move_optional_signal_step(state, "risk", (message.text or "").strip() or None, SignalBuilderStates.comment)
    await message.answer("Введите комментарий или пропустите:", reply_markup=skip_keyboard())


@router.callback_query(SignalBuilderStates.risk, F.data == "signal_builder:skip")
async def skip_risk(callback: CallbackQuery, state: FSMContext):
    await move_optional_signal_step(state, "risk", None, SignalBuilderStates.comment)
    await callback.message.edit_text("Введите комментарий или пропустите:", reply_markup=skip_keyboard())
    await callback.answer()


@router.message(SignalBuilderStates.comment)
async def signal_comment(message: Message, state: FSMContext):
    await move_optional_signal_step(state, "comment", (message.text or "").strip() or None, SignalBuilderStates.score)
    await message.answer("Введите оценку сетапа от 0 до 100 или пропустите:", reply_markup=skip_keyboard())


@router.callback_query(SignalBuilderStates.comment, F.data == "signal_builder:skip")
async def skip_comment(callback: CallbackQuery, state: FSMContext):
    await move_optional_signal_step(state, "comment", None, SignalBuilderStates.score)
    await callback.message.edit_text("Введите оценку сетапа от 0 до 100 или пропустите:", reply_markup=skip_keyboard())
    await callback.answer()


@router.message(SignalBuilderStates.score)
async def signal_score(message: Message, state: FSMContext):
    try:
        score = int((message.text or "").strip())
        if not 0 <= score <= 100:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое число от 0 до 100 или нажмите «Пропустить».")
        return
    await state.update_data(score=score)
    await state.set_state(SignalBuilderStates.audience)
    await message.answer("Кому отправить сигнал?", reply_markup=audience_keyboard())


@router.callback_query(SignalBuilderStates.score, F.data == "signal_builder:skip")
async def skip_score(callback: CallbackQuery, state: FSMContext):
    await state.update_data(score=None)
    await state.set_state(SignalBuilderStates.audience)
    await callback.message.edit_text("Кому отправить сигнал?", reply_markup=audience_keyboard())
    await callback.answer()


@router.callback_query(SignalBuilderStates.audience, F.data.startswith("signal_builder:audience:"))
async def signal_audience(callback: CallbackQuery, state: FSMContext):
    audience = callback.data.rsplit(":", 1)[1]
    await state.update_data(audience=audience)
    data = await state.get_data()
    preview = {
        "signal_id": "PREVIEW",
        "symbol": data["symbol"],
        "direction": data["direction"],
        "entry": data["entry"],
        "stop_loss": data["stop_loss"],
        "take_profit_1": data["take_profit_1"],
        "take_profit_2": data.get("take_profit_2"),
        "take_profit_3": data.get("take_profit_3"),
        "risk": data.get("risk"),
        "comment": data.get("comment"),
        "score": data.get("score"),
    }
    audience_names = {"all": "всем", "vip": "только VIP", "free": "только FREE"}
    await state.set_state(SignalBuilderStates.confirm)
    await callback.message.edit_text(
        f"{format_signal(preview)}\n\nАудитория: {audience_names[audience]}",
        reply_markup=signal_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(SignalBuilderStates.confirm, F.data == "signal_builder:send")
async def create_and_send_signal(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    data = await state.get_data()
    await callback.message.edit_text("⏳ Создаю сигнал и запускаю рассылку...")
    signal_id = create_signal(
        symbol=data["symbol"],
        direction=data["direction"],
        entry=data["entry"],
        stop_loss=data["stop_loss"],
        take_profit_1=data["take_profit_1"],
        take_profit_2=data.get("take_profit_2"),
        take_profit_3=data.get("take_profit_3"),
        risk=data.get("risk"),
        comment=data.get("comment"),
        score=data.get("score"),
    )

    create_trading_signal(
        source_signal_id=signal_id,
        symbol=data["symbol"],
        direction=data["direction"],
        entry_zone=data["entry"],
        stop_loss=data["stop_loss"],
        tp1=data["take_profit_1"],
        tp2=data.get("take_profit_2"),
        tp3=data.get("take_profit_3"),
        risk=data.get("risk"),
        comment=data.get("comment"),
        setup_score=int(data.get("score") or 0),
        audience=data.get("audience", "all"),
    )

    signal = get_signal(signal_id)
    if signal is None:
        await state.clear()
        await callback.message.edit_text("Не удалось создать сигнал.", reply_markup=admin_keyboard())
        await callback.answer()
        return
    result = await send_signal_to_users(callback.bot, signal, audience=data.get("audience", "all"))
    await state.clear()
    await callback.message.edit_text(
        f"{format_signal(signal)}\n\n"
        "✅ Рассылка завершена\n\n"
        f"VIP: {result['vip_sent']}\n"
        f"FREE: {result['free_sent']}\n"
        f"Без лимита: {result['limit_exhausted']}\n"
        f"Ошибок: {result['failed']}",
        reply_markup=signal_admin_keyboard(signal_id),
    )
    await callback.answer()


async def start_block_flow(callback: CallbackQuery, state: FSMContext, unblock: bool = False):
    await state.set_state(AdminStates.waiting_unblock_user if unblock else AdminStates.waiting_block_user)
    await callback.message.edit_text(
        "Введите Telegram ID пользователя:",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:block")
async def start_block(callback: CallbackQuery, state: FSMContext):
    if await ensure_admin_callback(callback):
        await start_block_flow(callback, state, False)


@router.callback_query(F.data == "admin:unblock")
async def start_unblock(callback: CallbackQuery, state: FSMContext):
    if await ensure_admin_callback(callback):
        await start_block_flow(callback, state, True)


@router.message(AdminStates.waiting_block_user)
async def process_block(message: Message, state: FSMContext):
    await process_block_value(message, state, True)


@router.message(AdminStates.waiting_unblock_user)
async def process_unblock(message: Message, state: FSMContext):
    await process_block_value(message, state, False)


async def process_block_value(message: Message, state: FSMContext, blocked: bool):
    if not await ensure_admin_message(message):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен числовой Telegram ID.")
        return
    if user_id == int(ADMIN_ID) and blocked:
        await message.answer("Нельзя заблокировать администратора.")
        return
    ok = set_blocked(user_id, blocked)
    await state.clear()
    action = "заблокирован" if blocked else "разблокирован"
    await message.answer(
        f"✅ Пользователь {user_id} {action}." if ok else "Пользователь не найден.",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data.startswith("admin:quick_vip:"))
async def quick_vip(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    ok = set_vip(user_id, 1)
    await callback.answer("VIP выдан." if ok else "Ошибка.", show_alert=True)


@router.callback_query(F.data.startswith("admin:quick_unvip:"))
async def quick_unvip(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    ok = set_vip(user_id, 0)
    await callback.answer("VIP снят." if ok else "Ошибка.", show_alert=True)


@router.callback_query(F.data.startswith("admin:quick_block:"))
async def quick_block(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    ok = set_blocked(user_id, True)
    await callback.answer("Пользователь заблокирован." if ok else "Ошибка.", show_alert=True)


@router.callback_query(F.data.startswith("admin:quick_unblock:"))
async def quick_unblock(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    ok = set_blocked(user_id, False)
    await callback.answer("Пользователь разблокирован." if ok else "Ошибка.", show_alert=True)


# Сохраняем совместимость со старыми командами.
@router.message(F.text.regexp(r"^/vip\s+\d+$"))
async def give_vip_command(message: Message):
    if not await ensure_admin_message(message):
        return
    user_id = int(message.text.split()[1])
    await message.answer("✅ VIP выдан." if set_vip(user_id, 1) else "Пользователь не найден.")


@router.message(F.text.regexp(r"^/unvip\s+\d+$"))
async def remove_vip_command(message: Message):
    if not await ensure_admin_message(message):
        return
    user_id = int(message.text.split()[1])
    await message.answer("✅ VIP снят." if set_vip(user_id, 0) else "Пользователь не найден.")


def tracking_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📡 Активные сигналы",
                    callback_data="admin:tracking_active",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Signal Analytics",
                    callback_data="admin:tracking_stats",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В админ-панель",
                    callback_data="admin:home",
                )
            ],
        ]
    )


def tracking_signal_keyboard(signal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ TP1",
                    callback_data=f"admin:signal_event:{signal_id}:tp1",
                ),
                InlineKeyboardButton(
                    text="✅ TP2",
                    callback_data=f"admin:signal_event:{signal_id}:tp2",
                ),
                InlineKeyboardButton(
                    text="✅ TP3",
                    callback_data=f"admin:signal_event:{signal_id}:tp3",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🛡 Безубыток",
                    callback_data=f"admin:signal_event:{signal_id}:be",
                ),
                InlineKeyboardButton(
                    text="❌ Stop Loss",
                    callback_data=f"admin:signal_event:{signal_id}:sl",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"admin:signal_edit_menu:{signal_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Отправить обновление",
                    callback_data=f"admin:signal_notify:{signal_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏁 Закрыть",
                    callback_data=f"admin:signal_event:{signal_id}:manual",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Активные сигналы",
                    callback_data="admin:tracking_active",
                )
            ],
        ]
    )


def format_tracking_signal(signal: dict) -> str:
    icon = (
        "🟢"
        if signal["direction"].upper() == "LONG"
        else "🔴"
    )

    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"  SIGNAL #{signal['id']}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{signal['symbol']}\n"
        f"{icon} {signal['direction'].upper()}\n\n"
        "LEVELS\n"
        f"Entry  {signal['entry_zone']}\n"
        f"Stop   {signal['stop_loss']}\n"
        f"TP1    {signal['tp1']}\n"
        f"TP2    {signal.get('tp2') or '—'}\n"
        f"TP3    {signal.get('tp3') or '—'}\n\n"
        "RISK MODEL\n"
        f"Risk   {signal.get('risk') or '—'}\n"
        f"Score  {signal.get('setup_score') or 0}/100\n\n"
        f"Status  {signal['status'].upper()}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


@router.callback_query(F.data == "admin:tracking")
async def open_tracking(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return

    await callback.message.edit_text(
        "📡 СОПРОВОЖДЕНИЕ СИГНАЛОВ\n\n"
        "Здесь можно отмечать TP, безубыток и Stop Loss.",
        reply_markup=tracking_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:tracking_active")
async def open_active_tracking(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return

    signals = get_active_trading_signals()

    if not signals:
        await callback.message.edit_text(
            "Активных сигналов пока нет.\n\n"
            "Создай новый сигнал через кнопку «📈 Создать сигнал».",
            reply_markup=tracking_menu_keyboard(),
        )
        await callback.answer()
        return

    rows = [
        [
            InlineKeyboardButton(
                text=f"#{item['id']} {item['symbol']} {item['direction']}",
                callback_data=f"admin:tracking_signal:{item['id']}",
            )
        ]
        for item in signals
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="admin:tracking",
            )
        ]
    )

    await callback.message.edit_text(
        "📡 Активные сигналы:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:tracking_signal:"))
async def open_tracking_signal(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return

    signal_id = int(callback.data.rsplit(":", 1)[1])
    signal = get_trading_signal(signal_id)

    if not signal:
        await callback.answer("Сигнал не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        format_tracking_signal(signal),
        reply_markup=tracking_signal_keyboard(signal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:signal_event:"))
async def process_tracking_event(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return

    parts = callback.data.split(":")
    signal_id = int(parts[2])
    event_type = parts[3]

    labels = {
        "tp1": "TP1 достигнут",
        "tp2": "TP2 достигнут",
        "tp3": "TP3 достигнут",
        "be": "Сделка переведена в безубыток",
        "sl": "Сработал Stop Loss",
        "manual": "Сигнал закрыт вручную",
    }

    signal = get_trading_signal(signal_id)
    if not signal:
        await callback.answer("Сигнал не найден.", show_alert=True)
        return

    add_signal_event(signal_id, event_type, labels[event_type])

    if event_type in {"tp3", "sl", "manual"}:
        close_trading_signal(signal_id, event_type)

    signal = get_trading_signal(signal_id)

    await callback.message.edit_text(
        format_tracking_signal(signal)
        + f"\n\n🔔 {labels[event_type]}",
        reply_markup=(
            tracking_signal_keyboard(signal_id)
            if signal["status"] == "active"
            else tracking_menu_keyboard()
        ),
    )
    await callback.answer(labels[event_type], show_alert=True)


@router.callback_query(F.data == "admin:tracking_stats")
async def open_tracking_stats(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return

    stats = get_signal_statistics()

    await callback.message.edit_text(
        "📊 СТАТИСТИКА СИГНАЛОВ\n\n"
        f"Всего сигналов: {stats['total']}\n"
        f"Активных: {stats['active']}\n"
        f"Закрытых: {stats['closed']}\n\n"
        f"✅ Прибыльных: {stats['wins']}\n"
        f"❌ Убыточных: {stats['losses']}\n"
        f"🛡 Безубыток: {stats['breakeven']}\n"
        f"📈 Win Rate: {stats['winrate']:.1f}%",
        reply_markup=tracking_menu_keyboard(),
    )
    await callback.answer()


def signal_edit_menu_keyboard(signal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Изменить вход",
                    callback_data=f"admin:signal_edit:{signal_id}:entry",
                ),
                InlineKeyboardButton(
                    text="🛑 Изменить SL",
                    callback_data=f"admin:signal_edit:{signal_id}:stop_loss",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ Изменить TP1",
                    callback_data=f"admin:signal_edit:{signal_id}:take_profit_1",
                ),
                InlineKeyboardButton(
                    text="✅ Изменить TP2",
                    callback_data=f"admin:signal_edit:{signal_id}:take_profit_2",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ Изменить TP3",
                    callback_data=f"admin:signal_edit:{signal_id}:take_profit_3",
                ),
                InlineKeyboardButton(
                    text="⚖️ Изменить риск",
                    callback_data=f"admin:signal_edit:{signal_id}:risk",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 Изменить комментарий",
                    callback_data=f"admin:signal_edit:{signal_id}:comment",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К сигналу",
                    callback_data=f"admin:tracking_signal:{signal_id}",
                )
            ],
        ]
    )


FIELD_LABELS = {
    "entry": "зону входа",
    "stop_loss": "Stop Loss",
    "take_profit_1": "TP1",
    "take_profit_2": "TP2",
    "take_profit_3": "TP3",
    "risk": "риск",
    "comment": "комментарий",
}


@router.callback_query(F.data.startswith("admin:signal_edit_menu:"))
async def open_signal_edit_menu(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return

    signal_id = int(callback.data.rsplit(":", 1)[1])
    signal = get_signal(signal_id)

    if not signal or signal["status"] != "active":
        await callback.answer(
            "Активный сигнал не найден.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "✏️ РЕДАКТИРОВАНИЕ СИГНАЛА\n\n"
        f"Сигнал #{signal_id} • {signal['symbol']}\n\n"
        "Выберите параметр:",
        reply_markup=signal_edit_menu_keyboard(signal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:signal_edit:"))
async def start_signal_edit(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not await ensure_admin_callback(callback):
        return

    _, _, signal_id_raw, field_name = callback.data.split(":", 3)
    signal_id = int(signal_id_raw)

    if field_name not in FIELD_LABELS:
        await callback.answer(
            "Неизвестное поле.",
            show_alert=True,
        )
        return

    signal = get_signal(signal_id)

    if not signal or signal["status"] != "active":
        await callback.answer(
            "Активный сигнал не найден.",
            show_alert=True,
        )
        return

    await state.set_state(SignalEditStates.waiting_for_value)
    await state.update_data(
        edit_signal_id=signal_id,
        edit_field_name=field_name,
    )

    extra = ""
    if field_name in {"take_profit_2", "take_profit_3", "comment"}:
        extra = "\n\nЧтобы очистить поле, отправьте: -"

    await callback.message.answer(
        f"Введите новое значение для «{FIELD_LABELS[field_name]}»."
        f"{extra}"
    )
    await callback.answer()


@router.message(SignalEditStates.waiting_for_value)
async def save_signal_edit(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    signal_id = int(data["edit_signal_id"])
    field_name = str(data["edit_field_name"])

    raw_value = (message.text or "").strip()
    value = None if raw_value == "-" else raw_value

    if not raw_value:
        await message.answer("Значение не может быть пустым.")
        return

    updated = update_signal_field(
        signal_id=signal_id,
        field_name=field_name,
        value=value,
    )

    await state.clear()

    if not updated:
        await message.answer(
            "Сигнал не найден или уже закрыт."
        )
        return

    signal = get_signal(signal_id)

    await message.answer(
        "✅ Параметр обновлён.\n\n"
        f"Сигнал #{signal_id}\n"
        f"Изменено: {FIELD_LABELS[field_name]}\n"
        f"Новое значение: {value or 'очищено'}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📢 Отправить обновление",
                        callback_data=f"admin:signal_notify:{signal_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📡 Открыть сигнал",
                        callback_data=f"admin:tracking_signal:{signal_id}",
                    )
                ],
            ]
        ),
    )


def format_signal_update_message(signal: dict) -> str:
    icon = (
        "🟢"
        if str(signal["direction"]).upper() == "LONG"
        else "🔴"
    )

    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "    SIGNAL UPDATE\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"#{signal['signal_id']}  {signal['symbol']}\n"
        f"{icon} {str(signal['direction']).upper()}\n\n"
        f"Entry  {signal['entry']}\n"
        f"Stop   {signal['stop_loss']}\n"
        f"TP1    {signal['take_profit_1']}\n"
        f"TP2    {signal['take_profit_2'] or '—'}\n"
        f"TP3    {signal['take_profit_3'] or '—'}\n"
        f"Risk   {signal['risk'] or '—'}\n\n"
        f"{signal['comment'] or 'Параметры сигнала обновлены.'}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


@router.callback_query(F.data.startswith("admin:signal_notify:"))
async def notify_signal_update(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return

    signal_id = int(callback.data.rsplit(":", 1)[1])
    signal = get_signal(signal_id)

    if not signal:
        await callback.answer(
            "Сигнал не найден.",
            show_alert=True,
        )
        return

    recipient_ids = get_signal_recipient_ids(signal_id)

    if not recipient_ids:
        await callback.answer(
            "У сигнала нет сохранённых получателей.",
            show_alert=True,
        )
        return

    text = format_signal_update_message(signal)
    sent = 0
    failed = 0

    for user_id in recipient_ids:
        try:
            await callback.bot.send_message(
                chat_id=user_id,
                text=text,
            )
            sent += 1
        except Exception:
            failed += 1

    await callback.answer(
        f"Обновление отправлено: {sent}. Ошибок: {failed}.",
        show_alert=True,
    )
