import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.utils.token import TokenValidationError
from aiogram.fsm.storage.memory import SimpleEventIsolation

from bot import db
from bot.config import ADMIN_IDS, BOT_TOKEN, LOG_DIR
from bot.handlers import add_listing, moderation, my_listings, search, start, subscriptions
from bot.middlewares import LangMiddleware
from bot.storage import SQLiteStorage


# start.bat doesn't restart the bot on this exit code: restarting can't fix a bad .env
CONFIG_ERROR_EXIT_CODE = 2


def config_error(message: str) -> None:
    logging.error("%s Fix the .env file and start the bot again.", message)
    sys.exit(CONFIG_ERROR_EXIT_CODE)


async def main() -> None:
    if not BOT_TOKEN:
        config_error("BOT_TOKEN is empty.")
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS is empty — nobody will see the moderation menu.")

    await db.init_db()

    try:
        bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        await bot.get_me()
    except (TokenValidationError, TelegramUnauthorizedError):
        config_error("Telegram rejected BOT_TOKEN.")
    # Dialog state is kept in SQLite, so half-filled listings survive restarts
    storage = SQLiteStorage()
    await storage.init()
    # Event isolation handles one user's updates one at a time, so photos sent
    # as an album don't overwrite each other in the wizard state.
    dp = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())
    dp.message.middleware(LangMiddleware())
    dp.callback_query.middleware(LangMiddleware())
    # Order matters: main-menu buttons must win over wizard text input
    dp.include_router(start.router)
    dp.include_router(moderation.router)
    dp.include_router(search.router)
    dp.include_router(my_listings.router)
    dp.include_router(subscriptions.router)
    dp.include_router(add_listing.router)

    await dp.start_polling(bot)


def setup_logging() -> None:
    """Log to the console window and to logs/bot.log (5 files x 1 MB, oldest dropped)."""
    LOG_DIR.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_DIR / "bot.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), file_handler],
    )


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
