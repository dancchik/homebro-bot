"""FSM storage in SQLite, so unfinished dialogs (e.g. a half-filled listing)
survive bot restarts. aiogram's built-in MemoryStorage loses them."""
import json
from collections.abc import Mapping
from typing import Any

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, StateType, StorageKey

from bot.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm_states (
    key        TEXT PRIMARY KEY,
    state      TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Dialogs untouched for this long are considered abandoned
ABANDONED_AFTER_DAYS = 30


class SQLiteStorage(BaseStorage):
    def __init__(self) -> None:
        self.key_builder = DefaultKeyBuilder(with_bot_id=True, with_destiny=True)

    async def init(self) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executescript(SCHEMA)
            await db.execute(
                "DELETE FROM fsm_states WHERE updated_at < datetime('now', ?)",
                (f"-{ABANDONED_AFTER_DAYS} days",),
            )
            await db.commit()

    async def _save(self, key: StorageKey, column: str, value: str | None) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                f"""
                INSERT INTO fsm_states (key, {column}) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    {column} = excluded.{column}, updated_at = datetime('now')
                """,
                (self.key_builder.build(key), value),
            )
            # Nothing left to remember — drop the row
            await db.execute(
                "DELETE FROM fsm_states WHERE key = ? AND state IS NULL AND data = '{}'",
                (self.key_builder.build(key),),
            )
            await db.commit()

    async def _load(self, key: StorageKey, column: str) -> str | None:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                f"SELECT {column} FROM fsm_states WHERE key = ?",
                (self.key_builder.build(key),),
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await self._save(key, "state", state.state if isinstance(state, State) else state)

    async def get_state(self, key: StorageKey) -> str | None:
        return await self._load(key, "state")

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._save(key, "data", json.dumps(dict(data), ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        raw = await self._load(key, "data")
        return json.loads(raw) if raw else {}

    async def close(self) -> None:
        pass  # connections are opened per call
