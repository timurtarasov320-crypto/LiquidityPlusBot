from __future__ import annotations

import html
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import activate_subscription, get_vip_until
from role_manager import has_role

router = Router()
DB_PATH = Path("v6_features.db")
SUPPORTED_LANGUAGES = {"ru": "Русский", "uk": "Українська", "en": "English"}
SUPPORTED_EXCHANGES = {"okx": "OKX", "bybit": "Bybit", "binance": "Binance", "bingx": "BingX"}

TEXTS = {
    "ru": {
        "settings": "Настройки профиля",
        "language": "Язык",
        "exchange": "Основная биржа",
        "saved": "Настройка сохранена.",
        "choose_language": "Выберите язык интерфейса:",
        "choose_exchange": "Выберите основную биржу:",
        "payment_created": "Заявка на оплату создана. Переведите {amount} USDT по сети {network} на адрес:\n<code>{address}</code>\n\nПосле перевода отправьте: /paid {payment_id} TXID",
        "payment_missing": "Заявка не найдена.",
        "payment_approved": "Оплата подтверждена. VIP активирован до {until}.",
        "payment_rejected": "Оплата отклонена администратором.",
    },
    "uk": {
        "settings": "Налаштування профілю",
        "language": "Мова",
        "exchange": "Основна біржа",
        "saved": "Налаштування збережено.",
        "choose_language": "Оберіть мову інтерфейсу:",
        "choose_exchange": "Оберіть основну біржу:",
        "payment_created": "Заявку на оплату створено. Перекажіть {amount} USDT у мережі {network} на адресу:\n<code>{address}</code>\n\nПісля переказу надішліть: /paid {payment_id} TXID",
        "payment_missing": "Заявку не знайдено.",
        "payment_approved": "Оплату підтверджено. VIP активний до {until}.",
        "payment_rejected": "Оплату відхилено адміністратором.",
    },
    "en": {
        "settings": "Profile settings",
        "language": "Language",
        "exchange": "Primary exchange",
        "saved": "Setting saved.",
        "choose_language": "Choose interface language:",
        "choose_exchange": "Choose your primary exchange:",
        "payment_created": "Payment request created. Send {amount} USDT via {network} to:\n<code>{address}</code>\n\nAfter transfer send: /paid {payment_id} TXID",
        "payment_missing": "Payment request not found.",
        "payment_approved": "Payment approved. VIP is active until {until}.",
        "payment_rejected": "Payment rejected by administrator.",
    },
}

PLANS = {
    "week": (5.0, 7),
    "month": (15.0, 30),
    "three_months": (40.0, 90),
    "six_months": (75.0, 180),
    "year": (145.0, 365),
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_v6_tables() -> None:
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER PRIMARY KEY,
            language TEXT NOT NULL DEFAULT 'ru',
            exchange_name TEXT NOT NULL DEFAULT 'okx',
            currency TEXT NOT NULL DEFAULT 'USD',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS manual_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USDT',
            network TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            txid TEXT,
            status TEXT NOT NULL DEFAULT 'created',
            created_at TEXT NOT NULL,
            submitted_at TEXT,
            reviewed_at TEXT,
            reviewed_by INTEGER
        );
        """
    )
    conn.commit()
    conn.close()


def _prefs(user_id: int) -> sqlite3.Row:
    create_v6_tables()
    conn = _connect()
    row = conn.execute("SELECT * FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO user_preferences(user_id,language,exchange_name,currency,updated_at) VALUES(?,?,?,?,?)",
            (user_id, "ru", "okx", "USD", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def _t(user_id: int, key: str, **kwargs) -> str:
    lang = str(_prefs(user_id)["language"])
    return TEXTS.get(lang, TEXTS["ru"])[key].format(**kwargs)


def _set_pref(user_id: int, field: str, value: str) -> None:
    if field not in {"language", "exchange_name", "currency"}:
        raise ValueError("Unsupported preference")
    _prefs(user_id)
    conn = _connect()
    conn.execute(
        f"UPDATE user_preferences SET {field}=?, updated_at=? WHERE user_id=?",
        (value, datetime.now(timezone.utc).isoformat(), user_id),
    )
    conn.commit()
    conn.close()


def exchange_url(exchange: str, symbol: str) -> str:
    exchange = exchange.lower()
    clean = symbol.upper().replace("-USDT-SWAP", "USDT").replace("/", "").replace("-", "")
    base = clean[:-4] if clean.endswith("USDT") else clean
    if exchange == "okx":
        return f"https://www.okx.com/trade-swap/{base.lower()}-usdt-swap"
    if exchange == "bybit":
        return f"https://www.bybit.com/trade/usdt/{clean}"
    if exchange == "binance":
        return f"https://www.binance.com/en/futures/{clean}"
    if exchange == "bingx":
        return f"https://bingx.com/en-us/perpetual/{base}-USDT"
    return f"https://www.tradingview.com/chart/?symbol={quote(clean)}"


def exchange_keyboard(user_id: int, symbol: str) -> InlineKeyboardMarkup:
    preferred = str(_prefs(user_id)["exchange_name"])
    ordered = [preferred] + [x for x in SUPPORTED_EXCHANGES if x != preferred]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Открыть {SUPPORTED_EXCHANGES[e]}", url=exchange_url(e, symbol))]
            for e in ordered
        ]
    )


@router.message(Command("settings_v6", "settings"))
async def settings_command(message: Message) -> None:
    p = _prefs(message.from_user.id)
    lang = str(p["language"])
    await message.answer(
        f"{TEXTS[lang]['settings']}\n\n{TEXTS[lang]['language']}: {SUPPORTED_LANGUAGES[lang]}\n{TEXTS[lang]['exchange']}: {SUPPORTED_EXCHANGES[str(p['exchange_name'])]}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["language"], callback_data="v6:language")],
            [InlineKeyboardButton(text=TEXTS[lang]["exchange"], callback_data="v6:exchange")],
        ]),
    )


@router.callback_query(F.data == "v6:language")
async def choose_language(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        _t(callback.from_user.id, "choose_language"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"v6:setlang:{code}")]
            for code, name in SUPPORTED_LANGUAGES.items()
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("v6:setlang:"))
async def set_language(callback: CallbackQuery) -> None:
    code = callback.data.rsplit(":", 1)[-1]
    if code not in SUPPORTED_LANGUAGES:
        await callback.answer("Unknown language", show_alert=True)
        return
    _set_pref(callback.from_user.id, "language", code)
    await callback.message.edit_text(TEXTS[code]["saved"])
    await callback.answer()


@router.callback_query(F.data == "v6:exchange")
async def choose_exchange(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        _t(callback.from_user.id, "choose_exchange"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"v6:setexchange:{code}")]
            for code, name in SUPPORTED_EXCHANGES.items()
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("v6:setexchange:"))
async def set_exchange(callback: CallbackQuery) -> None:
    code = callback.data.rsplit(":", 1)[-1]
    if code not in SUPPORTED_EXCHANGES:
        await callback.answer("Unknown exchange", show_alert=True)
        return
    _set_pref(callback.from_user.id, "exchange_name", code)
    await callback.message.edit_text(_t(callback.from_user.id, "saved"))
    await callback.answer()


@router.message(Command("trade"))
async def trade_link(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Формат: /trade BTCUSDT")
        return
    symbol = parts[1].upper()
    await message.answer(f"{html.escape(symbol)} — выберите биржу:", reply_markup=exchange_keyboard(message.from_user.id, symbol))


@router.message(Command("payusdt"))
async def create_manual_payment(message: Message, bot: Bot) -> None:
    parts = (message.text or "").split()
    plan = parts[1].lower() if len(parts) > 1 else "month"
    if plan not in PLANS:
        await message.answer("Формат: /payusdt week|month|three_months|six_months|year")
        return
    address = os.getenv("USDT_WALLET_ADDRESS", "").strip()
    network = os.getenv("USDT_NETWORK", "TRC20").strip() or "TRC20"
    if not address:
        await message.answer("Ручная оплата ещё не настроена владельцем. Добавьте USDT_WALLET_ADDRESS в .env.")
        return
    amount, _ = PLANS[plan]
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO manual_payments(user_id,plan,amount,network,wallet_address,created_at) VALUES(?,?,?,?,?,?)",
        (message.from_user.id, plan, amount, network, address, datetime.now(timezone.utc).isoformat()),
    )
    payment_id = int(cur.lastrowid)
    conn.commit(); conn.close()
    await message.answer(_t(message.from_user.id, "payment_created", amount=amount, network=network, address=html.escape(address), payment_id=payment_id), parse_mode="HTML")


@router.message(Command("paid"))
async def submit_manual_payment(message: Message, bot: Bot) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("Формат: /paid PAYMENT_ID TXID")
        return
    try:
        payment_id = int(parts[1]); txid = parts[2].strip()
    except ValueError:
        await message.answer("PAYMENT_ID должен быть числом.")
        return
    conn = _connect()
    row = conn.execute("SELECT * FROM manual_payments WHERE id=? AND user_id=?", (payment_id, message.from_user.id)).fetchone()
    if not row or row["status"] not in {"created", "submitted"}:
        conn.close(); await message.answer(_t(message.from_user.id, "payment_missing")); return
    conn.execute("UPDATE manual_payments SET txid=?,status='submitted',submitted_at=? WHERE id=?", (txid, datetime.now(timezone.utc).isoformat(), payment_id))
    conn.commit(); conn.close()
    admin_id = int(os.getenv("ADMIN_ID", "0") or 0)
    if not admin_id:
        try:
            from config import ADMIN_ID as configured_admin
            admin_id = int(configured_admin)
        except Exception:
            admin_id = 0
    if admin_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"v6:payapprove:{payment_id}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"v6:payreject:{payment_id}")]])
        try:
            await bot.send_message(admin_id, f"Новая USDT-оплата #{payment_id}\nUser: {message.from_user.id}\nPlan: {row['plan']}\nAmount: {row['amount']} USDT\nNetwork: {row['network']}\nTXID: {html.escape(txid)}", reply_markup=kb)
        except Exception:
            pass
    await message.answer("TXID отправлен на проверку.")


async def _review_payment(callback: CallbackQuery, approve: bool, bot: Bot) -> None:
    if not has_role(callback.from_user.id, "admin"):
        await callback.answer("Недостаточно прав", show_alert=True); return
    payment_id = int(callback.data.rsplit(":", 1)[-1])
    conn = _connect(); row = conn.execute("SELECT * FROM manual_payments WHERE id=?", (payment_id,)).fetchone()
    if not row or row["status"] != "submitted":
        conn.close(); await callback.answer("Заявка уже обработана или не найдена", show_alert=True); return
    now = datetime.now(timezone.utc)
    if approve:
        _, days = PLANS[str(row["plan"])]
        current = get_vip_until(int(row["user_id"])); base = current if current and current > now else now
        until = base + timedelta(days=days)
        if not activate_subscription(int(row["user_id"]), f"manual_usdt:{row['plan']}", until):
            conn.close(); await callback.answer("Пользователь не найден в users.db", show_alert=True); return
        status = "approved"
    else:
        until = None; status = "rejected"
    conn.execute("UPDATE manual_payments SET status=?,reviewed_at=?,reviewed_by=? WHERE id=?", (status, now.isoformat(), callback.from_user.id, payment_id))
    conn.commit(); conn.close()
    try:
        text = _t(int(row["user_id"]), "payment_approved", until=until.strftime("%d.%m.%Y")) if approve else _t(int(row["user_id"]), "payment_rejected")
        await bot.send_message(int(row["user_id"]), text)
    except Exception:
        pass
    await callback.message.edit_text((callback.message.text or "") + f"\n\nСтатус: {'ПОДТВЕРЖДЕНО' if approve else 'ОТКЛОНЕНО'}")
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("v6:payapprove:"))
async def approve_payment(callback: CallbackQuery, bot: Bot) -> None:
    await _review_payment(callback, True, bot)


@router.callback_query(F.data.startswith("v6:payreject:"))
async def reject_payment(callback: CallbackQuery, bot: Bot) -> None:
    await _review_payment(callback, False, bot)


@router.message(Command("payments_manual"))
async def list_manual_payments(message: Message) -> None:
    if not has_role(message.from_user.id, "moderator"):
        await message.answer("Недостаточно прав."); return
    conn = _connect(); rows = conn.execute("SELECT * FROM manual_payments ORDER BY id DESC LIMIT 20").fetchall(); conn.close()
    if not rows:
        await message.answer("Заявок пока нет."); return
    lines = ["Последние ручные оплаты:"]
    for r in rows:
        lines.append(f"#{r['id']} | {r['user_id']} | {r['plan']} | {r['amount']} USDT | {r['status']}")
    await message.answer("\n".join(lines))
