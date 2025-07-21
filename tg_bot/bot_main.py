import asyncio, logging
from aiogram import *
from aiogram import Bot, Dispatcher
import handlers
import os


async def main():
    logging.basicConfig(level = logging.INFO)
    token=os.getenv("BOT_TOKEN")
    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(handlers.router)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())