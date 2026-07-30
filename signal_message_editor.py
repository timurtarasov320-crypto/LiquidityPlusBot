from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from signals import (
    format_signal_rating,
    get_signal_recipient_messages,
    get_signal_user_action,
    signal_user_keyboard,
)


def _mark(done: bool) -> str:
    return "✅" if done else "⏳"


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_elapsed(value: object) -> str:
    created_at = _parse_datetime(value)
    if created_at is None:
        return "время неизвестно"

    seconds = max(0, int((datetime.now(timezone.utc) - created_at).total_seconds()))
    if seconds < 60:
        return "только что"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин. назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч. назад"
    days = hours // 24
    return f"{days} дн. назад"


def _resolve_status(signal: dict[str, Any], requested_status: str) -> str:
    stored_status = str(signal.get("status") or "active").lower()
    if stored_status in {"win", "loss", "breakeven", "expired", "cancelled"}:
        return stored_status
    if requested_status in {"tp1", "tp2", "tp3"}:
        return requested_status
    if not bool(signal.get("entry_reached")):
        return "waiting_entry"
    return "active"


def build_live_signal_text(signal: dict[str, Any], status: str) -> str:
    direction = str(signal.get("direction") or "").upper()
    direction_text = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    access_header = "📈 ТОРГОВЫЙ СИГНАЛ"

    tp1_hit = bool(signal.get("tp1_hit"))
    tp2_hit = bool(signal.get("tp2_hit"))
    tp3_hit = bool(signal.get("tp3_hit"))
    be_active = bool(signal.get("breakeven_active"))
    effective_status = _resolve_status(signal, status)

    status_map = {
        "waiting_entry": "🟡 Ожидает входа",
        "active": "🟢 Позиция активна",
        "tp1": "🎯 TP1 достигнут",
        "tp2": "🚀 TP2 достигнут",
        "tp3": "🏆 TP3 достигнут",
        "win": "🏆 Сигнал полностью отработан",
        "loss": "❌ Закрыт по Stop Loss",
        "breakeven": "🛡 Закрыт в безубыток",
        "expired": "⌛ Срок сигнала истёк",
        "cancelled": "🚫 Сигнал отменён",
    }

    lines = [
        f"{access_header} #{signal['signal_id']}",
        f"🕒 Опубликован: {format_elapsed(signal.get('created_at'))}",
        "",
        f"Монета: {signal['symbol']}",
        f"Направление: {direction_text}",
        f"Статус: {status_map.get(effective_status, status_map['active'])}",
    ]

    rating = format_signal_rating(signal.get("score"))
    if rating:
        lines.append(f"Рейтинг сигнала: {rating}")

    lines.extend([
        "",
        f"🎯 Вход: {signal['entry']}",
        f"🛑 Стоп: {'ТВХ (безубыток)' if be_active else signal['stop_loss']}",
        f"{_mark(tp1_hit)} TP1: {signal['take_profit_1']}",
    ])

    if signal.get("take_profit_2"):
        lines.append(f"{_mark(tp2_hit)} TP2: {signal['take_profit_2']}")
    if signal.get("take_profit_3"):
        lines.append(f"{_mark(tp3_hit)} TP3: {signal['take_profit_3']}")
    if signal.get("last_checked_price"):
        lines.extend(["", f"💱 Последняя цена: {signal['last_checked_price']}"])
    if signal.get("risk"):
        lines.extend(["", f"⚠️ Риск: {signal['risk']}"])
    if signal.get("comment"):
        lines.extend(["", "📝 Комментарий:\n" + str(signal["comment"])])

    notes = {
        "waiting_entry": "Цена ещё не вошла в указанную зону входа.",
        "tp1": "🔒 Стоп переносим в ТВХ.",
        "tp2": "Зафиксируйте ещё часть позиции.",
        "tp3": "Последняя цель достигнута.",
        "win": "Сигнал закрыт по последнему тейку.",
        "loss": "Сигнал закрыт по стопу.",
        "breakeven": "Остаток позиции закрыт без убытка.",
        "expired": "Мониторинг отключён: сигнал слишком старый.",
        "cancelled": "Мониторинг этого сигнала отключён.",
    }
    if effective_status in notes:
        lines.extend(["", notes[effective_status]])

    return "\n".join(lines)


async def update_signal_messages(
    bot: Bot,
    signal: dict[str, Any],
    status: str,
) -> tuple[int, int]:
    recipients = get_signal_recipient_messages(int(signal["signal_id"]))
    text = build_live_signal_text(signal, status)
    edited = 0
    failed = 0

    for recipient in recipients:
        try:
            await bot.edit_message_text(
                chat_id=int(recipient["chat_id"]),
                message_id=int(recipient["message_id"]),
                text=text,
                reply_markup=signal_user_keyboard(
                    str(signal["symbol"]),
                    int(signal["signal_id"]),
                    show_decision_buttons=(
                        get_signal_user_action(
                            int(signal["signal_id"]),
                            int(recipient["user_id"]),
                        ) is None
                    ),
                ),
            )
            edited += 1
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                failed += 1
                print(f"Не удалось обновить сигнал #{signal['signal_id']}: {error}")
        except TelegramForbiddenError:
            failed += 1
        except Exception as error:
            failed += 1
            print(f"Ошибка редактирования сигнала #{signal['signal_id']}: {error}")
        await asyncio.sleep(0.04)

    return edited, failed
