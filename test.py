import asyncio
from aiogram import Bot

TOKEN = "8931692248:AAESqPXSg1mzmd9lah2xNzgnfXF58zWE6Pw"

async def main():
    bot = Bot(TOKEN)
    me = await bot.get_me()
    print(me)
    await bot.session.close()

asyncio.run(main())