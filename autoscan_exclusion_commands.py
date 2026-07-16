from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from autoscan_exclusions import (
    clear_excluded_markets,
    exclude_market,
    get_excluded_markets,
    get_exclusion_info,
    include_market,
    normalize_inst_id,
)
from config import ADMIN_ID


router = Router()


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def format_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return value


@router.message(Command("exclude"))
async def exclude_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    raw_text = message.text.partition(" ")[2].strip()

    if not raw_text:
        await message.answer(
            "Использование:\n\n"
            "/exclude DOGE\n"
            "/exclude DOGE плохая статистика\n"
            "/exclude DOGE-USDT-SWAP слишком высокий спред"
        )
        return

    parts = raw_text.split(maxsplit=1)

    market_text = parts[0]
    reason = (
        parts[1].strip()
        if len(parts) > 1
        else None
    )

    inst_id = normalize_inst_id(market_text)

    added = exclude_market(
        inst_id=inst_id,
        reason=reason,
    )

    if added:
        await message.answer(
            "✅ Монета исключена из автосканера\n\n"
            f"Инструмент: {inst_id}\n"
            f"Причина: {reason or 'не указана'}"
        )
    else:
        await message.answer(
            "ℹ️ Монета уже была исключена.\n\n"
            f"Инструмент: {inst_id}\n"
            f"Причина обновлена: {reason or 'без изменений'}"
        )


@router.message(Command("include"))
async def include_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    raw_text = message.text.partition(" ")[2].strip()

    if not raw_text:
        await message.answer(
            "Использование:\n"
            "/include DOGE"
        )
        return

    inst_id = normalize_inst_id(raw_text)

    removed = include_market(inst_id)

    if removed:
        await message.answer(
            "✅ Монета возвращена в автосканер\n\n"
            f"Инструмент: {inst_id}"
        )
    else:
        await message.answer(
            "Монета не находилась в списке исключений.\n\n"
            f"Инструмент: {inst_id}"
        )


@router.message(Command("excludelist"))
async def exclusion_list_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    excluded = get_excluded_markets()

    if not excluded:
        await message.answer(
            "Список исключённых монет пуст."
        )
        return

    lines = [
        "🚫 Исключённые монеты",
        "",
        f"Всего: {len(excluded)}",
        "",
    ]

    for index, item in enumerate(
        excluded[:100],
        start=1,
    ):
        reason = item["reason"] or "без причины"
        created_at = format_datetime(
            item["created_at"]
        )

        lines.append(
            f"{index}. {item['inst_id']}\n"
            f"Причина: {reason}\n"
            f"Добавлено: {created_at}\n"
        )

    await message.answer("\n".join(lines))


@router.message(Command("excludeinfo"))
async def exclusion_info_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    raw_text = message.text.partition(" ")[2].strip()

    if not raw_text:
        await message.answer(
            "Использование:\n"
            "/excludeinfo DOGE"
        )
        return

    inst_id = normalize_inst_id(raw_text)
    info = get_exclusion_info(inst_id)

    if info is None:
        await message.answer(
            "Монета не исключена.\n\n"
            f"Инструмент: {inst_id}"
        )
        return

    await message.answer(
        "🚫 Информация об исключении\n\n"
        f"Инструмент: {info['inst_id']}\n"
        f"Причина: {info['reason'] or 'не указана'}\n"
        f"Дата: {format_datetime(info['created_at'])}"
    )


@router.message(Command("excludeclear"))
async def exclusion_clear_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    deleted = clear_excluded_markets()

    await message.answer(
        "✅ Список исключений очищен.\n\n"
        f"Удалено записей: {deleted}"
    )