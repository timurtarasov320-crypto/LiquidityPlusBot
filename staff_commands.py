from __future__ import annotations

import asyncio
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from backup_manager import create_backup
from role_manager import get_role, has_role, list_staff, remove_role, set_role

router = Router()


@router.message(Command("myrole"))
async def my_role(message: Message):
    role = get_role(message.from_user.id) if message.from_user else None
    await message.answer(f"Ваша роль: {role or 'пользователь'}")


@router.message(Command("staff"))
async def staff_list(message: Message):
    if not message.from_user or not has_role(message.from_user.id, "admin"):
        return
    rows = list_staff()
    text = "👥 КОМАНДА\n\n" + "\n".join(f"{uid} — {role}" for uid, role, _ in rows)
    await message.answer(text)


@router.message(Command("setrole"))
async def set_role_command(message: Message):
    if not message.from_user or not has_role(message.from_user.id, "owner"):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Использование: /setrole USER_ID owner|admin|moderator|analyst")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("USER_ID должен быть числом.")
        return
    ok = set_role(message.from_user.id, target_id, parts[2])
    await message.answer("✅ Роль сохранена." if ok else "❌ Не удалось изменить роль.")


@router.message(Command("delrole"))
async def delete_role_command(message: Message):
    if not message.from_user or not has_role(message.from_user.id, "owner"):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Использование: /delrole USER_ID")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("USER_ID должен быть числом.")
        return
    ok = remove_role(message.from_user.id, target_id)
    await message.answer("✅ Роль удалена." if ok else "❌ Не удалось удалить роль.")


@router.message(Command("backup"))
async def backup_command(message: Message):
    if not message.from_user or not has_role(message.from_user.id, "owner"):
        return
    status = await message.answer("⏳ Создаю резервную копию...")
    path = await asyncio.to_thread(create_backup)
    await status.edit_text("✅ Резервная копия создана.")
    await message.answer_document(FSInputFile(path), caption="LiquidityPlus backup")
