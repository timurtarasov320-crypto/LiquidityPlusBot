from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CHANNELS

router = Router()


async def check_sub(bot, user_id: int):
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)

            if member.status in ("left", "kicked"):
                return False

        except Exception:
            return False

    return True


def sub_keyboard():
    kb = InlineKeyboardBuilder()

    for channel in CHANNELS:
        kb.button(
            text=f"📢 {channel}",
            url=f"https://t.me/{channel.replace('@','')}"
        )

    kb.button(
        text="✅ Проверить подписку",
        callback_data="check_sub"
    )

    kb.adjust(1)

    return kb.as_markup()


@router.callback_query(F.data == "check_sub")
async def check(callback: CallbackQuery):

    status = await check_sub(
        callback.bot,
        callback.from_user.id
    )

    if status:

        await callback.message.edit_text(
            "✅ Подписка подтверждена.\n\nТеперь можете пользоваться ботом."
        )

    else:

        await callback.answer(
            "Вы подписались не на все каналы.",
            show_alert=True
        )