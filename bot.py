import asyncio
import os
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher

from admin import router as admin_router
from admin_web import start_admin_web_server
from backup_manager import automatic_backup_loop
from logging_config import setup_logging
from role_manager import create_role_tables
from staff_commands import router as staff_router
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
from subscription_lifecycle import subscription_lifecycle_monitor
from signals import router as signals_router
from v5_features import router as v5_router, create_v5_tables, automatic_reports
from v6_features import router as v6_router, create_v6_tables




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

setup_logging()
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher()

dp.update.outer_middleware(BlockedUsersMiddleware())

dp.include_router(v6_router)
dp.include_router(v5_router)
dp.include_router(staff_router)
dp.include_router(admin_router)
dp.include_router(payments_router)
dp.include_router(signals_router)
dp.include_router(signal_analytics_router)
dp.include_router(market_assistant_router)
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
    create_role_tables()
    create_v5_tables()
    create_v6_tables()

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


    subscription_task = asyncio.create_task(
        subscription_lifecycle_monitor(bot)
    )

    backup_task = asyncio.create_task(
        automatic_backup_loop()
    )

    reports_task = asyncio.create_task(
        automatic_reports(bot, ADMIN_ID)
    )

    admin_web_runner = await start_admin_web_server()
    if admin_web_runner:
        print("Веб-панель администратора запущена")

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
        subscription_task.cancel()
        backup_task.cancel()
        reports_task.cancel()

        for task in (
            monitor_task,
            scanner_task,
            analytics_task,
            payment_task,
            subscription_task,
            backup_task,
            reports_task,
        ):
            try:
                await task
            except asyncio.CancelledError:
                pass

        if admin_web_runner:
            await admin_web_runner.cleanup()

        await order_flow_ws.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())