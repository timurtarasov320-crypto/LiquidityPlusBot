import asyncio
import os

from aiogram import BaseMiddleware, Bot, Dispatcher

from admin import router as admin_router
from admin_audit import create_audit_table
from auto_scanner import automatic_market_scanner
from auto_scanner_commands import (
    router as auto_scanner_router,
)
from autoscan_exclusion_commands import (
    router as autoscan_exclusion_router,
)
from channels import router as channels_router
from config import ADMIN_ID, TOKEN
from database import create_tables, is_user_blocked
from handlers import router as handlers_router
from market_assistant import (
    router as market_assistant_router,
)
from market_hub import router as market_hub_router
from order_flow_commands import (
    router as order_flow_commands_router,
)
from order_flow_ws import order_flow_ws
from payments import (
    automatic_payment_monitor,
    router as payments_router,
)
from signal_analytics import daily_analytics_report
from signal_analytics_commands import (
    router as signal_analytics_router,
)
from signal_monitor import monitor_signals
from signals import router as signals_router




class BlockedUsersMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and int(user.id) != int(ADMIN_ID) and is_user_blocked(user.id):
            if hasattr(event, "answer"):
                try:
                    await event.answer("Доступ к боту заблокирован.")
                except Exception:
                    pass
            return None
        return await handler(event, data)

bot = Bot(token=TOKEN)
dp = Dispatcher()

dp.update.outer_middleware(BlockedUsersMiddleware())

dp.include_router(admin_router)
dp.include_router(payments_router)
dp.include_router(signals_router)
dp.include_router(signal_analytics_router)
dp.include_router(market_assistant_router)
dp.include_router(market_hub_router)
dp.include_router(auto_scanner_router)
dp.include_router(autoscan_exclusion_router)
dp.include_router(order_flow_commands_router)
dp.include_router(channels_router)
dp.include_router(handlers_router)


DEFAULT_ORDER_FLOW_MARKETS = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP",
    "AVAX-USDT-SWAP",
    "LINK-USDT-SWAP",
    "SUI-USDT-SWAP",
    "ZEC-USDT-SWAP",
]


async def main():
    create_tables()
    create_audit_table()

    print("ПАПКА ПРОЕКТА:", os.getcwd())
    print("ФАЙЛ BOT:", os.path.abspath(__file__))

    me = await bot.get_me()

    print(f"Бот подключен: @{me.username}")

    await order_flow_ws.start(
        DEFAULT_ORDER_FLOW_MARKETS
    )

    print(
        "LIVE Order Flow запущен для "
        f"{len(DEFAULT_ORDER_FLOW_MARKETS)} монет"
    )

    monitor_task = asyncio.create_task(
        monitor_signals(bot)
    )

    scanner_task = asyncio.create_task(
        automatic_market_scanner(bot)
    )

    analytics_task = asyncio.create_task(
        daily_analytics_report(bot)
    )

    payment_task = asyncio.create_task(
        automatic_payment_monitor(bot)
    )

    print("Автоматический сканер запущен")
    print("Ежедневная аналитика запущена")
    print("Автоматическая проверка оплат запущена")

    try:
        await dp.start_polling(
            bot,
            allowed_updates=(
                dp.resolve_used_update_types()
            ),
        )

    finally:
        monitor_task.cancel()
        scanner_task.cancel()
        analytics_task.cancel()
        payment_task.cancel()

        for task in (
            monitor_task,
            scanner_task,
            analytics_task,
            payment_task,
        ):
            try:
                await task
            except asyncio.CancelledError:
                pass

        await order_flow_ws.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())