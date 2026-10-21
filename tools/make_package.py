"""Build a zip archive of the bot in dist/.

    python tools/make_package.py              # code only, safe to share
    python tools/make_package.py --with-data  # + .env (token) and bot.db: move or back up the bot
"""
import argparse
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = (
    "requirements.txt", ".env.example", ".gitattributes", "README.md",
    "install.bat", "start.bat", "package.bat",
)
DIRS = ("bot", "tools")
# Everything goes into this folder inside the archive, so unzipping makes one folder
ARCHIVE_DIR = "homebro-bot"


def add_code(archive: zipfile.ZipFile) -> None:
    for name in FILES:
        archive.write(ROOT / name, f"{ARCHIVE_DIR}/{name}")
    for folder in DIRS:
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, f"{ARCHIVE_DIR}/{path.relative_to(ROOT).as_posix()}")


def add_data(archive: zipfile.ZipFile) -> None:
    archive.write(ROOT / ".env", f"{ARCHIVE_DIR}/.env")
    db_path = ROOT / "bot.db"
    if not db_path.exists():
        print("bot.db not found - the archive has no database.")
        return
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "bot.db"
        # SQLite's backup API makes a consistent copy even while the bot is running
        source, target = sqlite3.connect(db_path), sqlite3.connect(copy_path)
        with target:
            source.backup(target)
        source.close()
        target.close()
        archive.write(copy_path, f"{ARCHIVE_DIR}/bot.db")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-data", action="store_true", help="include .env and bot.db")
    args = parser.parse_args()

    if args.with_data and not (ROOT / ".env").exists():
        sys.exit(".env not found - nothing to move.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    suffix = "-with-data" if args.with_data else ""
    out = ROOT / "dist" / f"homebro-bot-{stamp}{suffix}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        add_code(archive)
        if args.with_data:
            add_data(archive)

    print(f"Archive created: {out}")
    if args.with_data:
        print("WARNING: the archive contains the bot token and users' phone numbers.")
        print("Keep it private and don't send it to anyone.")


if __name__ == "__main__":
    main()
