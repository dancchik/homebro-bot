import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Comma-separated list of Telegram user IDs, e.g. "123,456"
ADMIN_IDS = {
    int(part) for part in os.getenv("ADMIN_IDS", "").split(",") if part.strip()
}

# Project folder (the one containing bot/), so paths don't depend on where the bot is started
ROOT_DIR = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.getenv("DB_PATH", ROOT_DIR / "bot.db"))
LOG_DIR = ROOT_DIR / "logs"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS
