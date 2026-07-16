from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_ID
from order_flow import format_money
from order_flow_ws import order_flow_ws

router = Router()


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def normalize_inst_id(value: str) -> str:
    text = (
        value.upper()
        .strip()
        .replace("/", "-")
        .replace("_", "-")
    )

    if text.endswith("-SWAP"):
        return text

    if text.endswith("-USDT"):
        return f"{text}-SWAP"

    if "-" not in text:
        return f"{text}-USDT-SWAP"

    return text


def format_snapshot(snapshot: dict) -> str:
    direction_names = {
        "bullish": "🟢 Покупатели сильнее",
        "bearish": "🔴 Продавцы сильнее",
        "neutral": "⚪ Баланс сторон",
    }

    updated_at = snapshot.get("updated_at", 0)

    data_status = (
        "Данные поступают"
        if updated_at
        else "Выборка ещё не накоплена"
    )

    return (
        "📡 LIVE ORDER FLOW\n\n"
        f"Инструмент: {snapshot['inst_id']}\n"
        f"Статус: {data_status}\n"
        f"Окно анализа: "
        f"{snapshot['window_minutes']} минут\n\n"
        f"Состояние: "
        f"{direction_names.get(snapshot['direction'], 'неизвестно')}\n"
        f"Оценка: {snapshot['score']:+d}/100\n\n"
        f"Сделок: {snapshot['total_trades']}\n"
        f"Покупок: {snapshot['buy_trades']}\n"
        f"Продаж: {snapshot['sell_trades']}\n\n"
        f"Объём покупок: "
        f"{format_money(snapshot['buy_volume'])}\n"
        f"Объём продаж: "
        f"{format_money(snapshot['sell_volume'])}\n"
        f"Общий объём: "
        f"{format_money(snapshot['total_volume'])}\n\n"
        f"Delta: {format_money(snapshot['delta'])}\n"
        f"Delta %: {snapshot['delta_percent']:+.2f}%\n"
        f"CVD: {format_money(snapshot['cvd'])}\n"
        f"Последняя цена: {snapshot['last_price']}\n\n"
        "После запуска сборщику нужно несколько минут, "
        "чтобы накопить нормальную выборку."
    )


@router.message(Command("orderflow"))
async def order_flow_info(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2:
        await message.answer(
            "Использование:\n\n"
            "/orderflow BTC\n"
            "/orderflow ETH-USDT-SWAP\n"
            "/orderflow SOL/USDT"
        )
        return

    inst_id = normalize_inst_id(parts[1])

    snapshot = order_flow_ws.get_snapshot(inst_id)

    if snapshot is None:
        await message.answer(
            "Инструмент сейчас не отслеживается.\n\n"
            f"Инструмент: {inst_id}\n\n"
            "Добавьте его командой:\n"
            f"/ofadd {inst_id}"
        )
        return

    await message.answer(
        format_snapshot(snapshot)
    )


@router.message(Command("orderflowstatus"))
async def order_flow_status(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    snapshots = order_flow_ws.get_all_snapshots()

    if not snapshots:
        await message.answer(
            "WebSocket Order Flow пока не отслеживает монеты."
        )
        return

    lines = [
        "📡 Статус LIVE Order Flow",
        "",
        f"Отслеживается монет: {len(snapshots)}",
        "",
    ]

    for snapshot in snapshots[:30]:
        lines.append(
            f"{snapshot['inst_id']} | "
            f"{snapshot['direction']} | "
            f"{snapshot['score']:+d} | "
            f"сделок: {snapshot['total_trades']}"
        )

    await message.answer("\n".join(lines))


@router.message(Command("ofadd"))
async def add_order_flow_market(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/ofadd BTC-USDT-SWAP"
        )
        return

    inst_id = normalize_inst_id(parts[1])

    markets = set(order_flow_ws.markets)
    markets.add(inst_id)

    await order_flow_ws.update_subscriptions(
        sorted(markets)
    )

    await message.answer(
        f"✅ Добавлено наблюдение за {inst_id}.\n\n"
        "Важно: новое соединение применит подписки "
        "после следующего переподключения WebSocket."
    )


@router.message(Command("ofremove"))
async def remove_order_flow_market(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/ofremove BTC-USDT-SWAP"
        )
        return

    inst_id = normalize_inst_id(parts[1])

    markets = set(order_flow_ws.markets)
    markets.discard(inst_id)

    await order_flow_ws.update_subscriptions(
        sorted(markets)
    )

    await message.answer(
        f"✅ {inst_id} удалён из списка наблюдения.\n\n"
        "Изменение полностью применится после "
        "переподключения WebSocket."
    )