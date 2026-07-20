from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from signals import get_signal_recipient_messages, signal_user_keyboard


def _mark(done: bool) -> str:
    return "✅" if done else "⏳"


def build_live_signal_text(signal: dict[str, Any], status: str) -> str:
    direction = str(signal.get("direction") or "").upper()
    direction_text = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    access_header = "📈 ТОРГОВЫЙ СИГНАЛ"

    tp1_hit = bool(signal.get("tp1_hit"))
    tp2_hit = bool(signal.get("tp2_hit"))
    tp3_hit = bool(signal.get("tp3_hit"))
    be_active = bool(signal.get("breakeven_active"))

    status_map = {
        "active": "🟢 Активен",
        "tp1": "🎯 TP1 достигнут",
        "tp2": "🚀 TP2 достигнут",
        "win": "🏆 Сигнал полностью отработан",
        "loss": "❌ Закрыт по Stop Loss",
        "breakeven": "🛡 Закрыт в безубыток",
    }

    lines = [
        f"{access_header} #{signal['signal_id']}",
        "",
        f"Монета: {signal['symbol']}",
        f"Направление: {direction_text}",
        f"Статус: {status_map.get(status, status_map['active'])}",
        "",
        f"🎯 Вход: {signal['entry']}",
        f"🛑 Стоп: {'ТВХ (безубыток)' if be_active else signal['stop_loss']}",
        f"{_mark(tp1_hit)} TP1: {signal['take_profit_1']}",
    ]

    if signal.get("take_profit_2"):
        lines.append(f"{_mark(tp2_hit)} TP2: {signal['take_profit_2']}")
    if signal.get("take_profit_3"):
        lines.append(f"{_mark(tp3_hit)} TP3: {signal['take_profit_3']}")
    if signal.get("risk"):
        lines.extend(["", f"⚠️ Риск: {signal['risk']}"])
    if signal.get("comment"):
        lines.extend(["", f"📝 Комментарий:\n{signal['comment']}"])

    if status == "tp1":
        lines.extend(["", "🔒 Стоп переносим в ТВХ."])
    elif status == "tp2":
        lines.extend(["", "Зафиксируйте ещё часть позиции."])
    elif status == "win":
        lines.extend(["", "Сигнал закрыт по последнему тейку."])
    elif status == "loss":
        lines.extend(["", "Сигнал закрыт по стопу."])
    elif status == "breakeven":
        lines.extend(["", "Остаток позиции закрыт без убытка."])

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
                reply_markup=signal_user_keyboard(str(signal["symbol"])),
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
