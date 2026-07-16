from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_ID
from signal_analytics import (
    format_statistics,
    get_best_coins,
    get_statistics_for_period,
)


router = Router()


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


@router.message(Command("analytics"))
async def analytics_help(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    await message.answer(
        "📊 Аналитика сигналов\n\n"
        "/analyticsday — за сегодня\n"
        "/analyticsweek — за неделю\n"
        "/analyticsmonth — за месяц\n"
        "/bestcoins — лучшие монеты"
    )


@router.message(Command("analyticsday"))
async def analytics_day(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    statistics = get_statistics_for_period("day")

    await message.answer(
        format_statistics(
            statistics,
            "Статистика за сегодня",
        )
    )


@router.message(Command("analyticsweek"))
async def analytics_week(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    statistics = get_statistics_for_period("week")

    await message.answer(
        format_statistics(
            statistics,
            "Статистика за неделю",
        )
    )


@router.message(Command("analyticsmonth"))
async def analytics_month(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    statistics = get_statistics_for_period("month")

    await message.answer(
        format_statistics(
            statistics,
            "Статистика за месяц",
        )
    )


@router.message(Command("bestcoins"))
async def best_coins(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    coins = get_best_coins(10)

    if not coins:
        await message.answer(
            "Закрытых сигналов пока нет."
        )
        return

    lines = ["🏆 Лучшие монеты", ""]

    for index, coin in enumerate(
        coins,
        start=1,
    ):
        lines.append(
            f"{index}. {coin['symbol']}\n"
            f"Сделок: {coin['total']} | "
            f"Плюс: {coin['wins']} | "
            f"Минус: {coin['losses']}\n"
            f"WinRate: {coin['winrate']:.2f}% | "
            f"Результат: {coin['result']:+.2f}%\n"
        )

    await message.answer("\n".join(lines))