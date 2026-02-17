"""
Bot entry point: aiogram 3.x, registers handlers and starts polling.
Can be run standalone or as a background task from main.py.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from bot.handlers import admin, user, withdraw

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_bot():
    """Run bot polling (for use inside FastAPI lifespan or standalone)."""
    import os
    if os.environ.get("RUN_BOT", "true").lower() == "false":
        logger.info("Bot disabled via RUN_BOT=false")
        return
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin.router)
    dp.include_router(user.router)
    dp.include_router(withdraw.router)
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    except TelegramConflictError as e:
        logger.warning(f"Bot conflict detected - another instance is running. Skipping bot. Error: {e}")
        return
    except Exception as e:
        logger.error(f"Bot polling error: {e}")
        # Якщо конфлікт в тексті помилки - також виходимо
        if "Conflict" in str(e) or "TelegramConflictError" in str(type(e).__name__):
            logger.warning("Bot conflict detected - another instance is running. Skipping bot.")
            return
        raise


if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_bot())
