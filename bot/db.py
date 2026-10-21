import json
from datetime import date

import aiosqlite

from bot.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    lang        TEXT,
    phone       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- status: pending -> approved / rejected; approved <-> rented; any -> deleted
CREATE TABLE IF NOT EXISTS listings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id       INTEGER NOT NULL REFERENCES users(telegram_id),
    status         TEXT NOT NULL DEFAULT 'pending',
    sector         TEXT NOT NULL,
    street         TEXT,
    rooms          TEXT NOT NULL,
    area           INTEGER NOT NULL,
    floor          INTEGER NOT NULL,
    floors_total   INTEGER NOT NULL,
    building       TEXT NOT NULL,
    heating        TEXT NOT NULL,
    renovation     TEXT NOT NULL,
    furniture      TEXT NOT NULL,
    amenities      TEXT NOT NULL DEFAULT '',  -- comma-separated codes
    terms          TEXT NOT NULL,             -- comma-separated codes
    price_month    INTEGER,
    price_day      INTEGER,
    deposit        INTEGER,
    utilities      TEXT NOT NULL,
    available_from TEXT,                      -- ISO date, NULL = available now
    description    TEXT,
    moderator_id   INTEGER,
    reject_reason  TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);

CREATE TABLE IF NOT EXISTS listing_photos (
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    file_id    TEXT NOT NULL,
    PRIMARY KEY (listing_id, position)
);

-- Ticket messages sent to admins, so all copies can be updated after a decision
CREATE TABLE IF NOT EXISTS ticket_messages (
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    admin_id   INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (listing_id, admin_id, message_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id    INTEGER NOT NULL,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, listing_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    filters    TEXT NOT NULL,  -- JSON, same format as users.search_filters
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, filters)
);

-- Which listings a user was already notified about, so nobody gets the same one twice
CREATE TABLE IF NOT EXISTS sent_notifications (
    user_id    INTEGER NOT NULL,
    listing_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, listing_id)
);
"""

# Columns added after the first release; created on startup if missing.
EXTRA_COLUMNS = {
    "users": {
        "first_name": "TEXT",
        "username": "TEXT",
        "search_filters": "TEXT",  # JSON with the user's last search filters
    },
    "listings": {
        "edited": "INTEGER NOT NULL DEFAULT 0",  # 1 = sent for review again after changes
    },
}

LISTING_FIELDS = (
    "sector", "street", "rooms", "area", "floor", "floors_total", "building",
    "heating", "renovation", "furniture", "amenities", "terms", "price_month",
    "price_day", "deposit", "utilities", "available_from", "description",
)
LIST_FIELDS = ("amenities", "terms")


def _connect() -> aiosqlite.Connection:
    return aiosqlite.connect(DB_PATH)


async def init_db() -> None:
    async with _connect() as db:
        await db.executescript(SCHEMA)
        for table, columns in EXTRA_COLUMNS.items():
            cursor = await db.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in await cursor.fetchall()}
            for column, decl in columns.items():
                if column not in existing:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        await db.commit()


# --- Users ---

async def get_lang(telegram_id: int) -> str | None:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT lang FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def set_lang(telegram_id: int, lang: str) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, lang) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET lang = excluded.lang
            """,
            (telegram_id, lang),
        )
        await db.commit()


async def get_phone(telegram_id: int) -> str | None:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT phone FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def save_contact(
    telegram_id: int, phone: str, first_name: str, username: str | None
) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, phone, first_name, username)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                phone = excluded.phone,
                first_name = excluded.first_name,
                username = excluded.username
            """,
            (telegram_id, phone, first_name, username),
        )
        await db.commit()


# --- Listings ---

async def create_listing(owner_id: int, data: dict, photos: list[str]) -> int:
    values = [
        ",".join(data.get(f) or []) if f in LIST_FIELDS else data.get(f)
        for f in LISTING_FIELDS
    ]
    placeholders = ", ".join("?" for _ in range(len(LISTING_FIELDS) + 1))
    async with _connect() as db:
        cursor = await db.execute(
            f"INSERT INTO listings (owner_id, {', '.join(LISTING_FIELDS)}) "
            f"VALUES ({placeholders})",
            (owner_id, *values),
        )
        listing_id = cursor.lastrowid
        await db.executemany(
            "INSERT INTO listing_photos (listing_id, position, file_id) VALUES (?, ?, ?)",
            [(listing_id, i, file_id) for i, file_id in enumerate(photos)],
        )
        await db.commit()
    return listing_id


async def get_listing(listing_id: int) -> dict | None:
    """Listing with its photos and owner contact info, or None."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT l.*, u.first_name AS owner_name, u.username AS owner_username,
                   u.phone AS owner_phone, u.lang AS owner_lang
            FROM listings l JOIN users u ON u.telegram_id = l.owner_id
            WHERE l.id = ?
            """,
            (listing_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        listing = dict(row)
        cursor = await db.execute(
            "SELECT file_id FROM listing_photos WHERE listing_id = ? ORDER BY position",
            (listing_id,),
        )
        listing["photos"] = [r["file_id"] for r in await cursor.fetchall()]
    for f in LIST_FIELDS:
        listing[f] = [code for code in (listing[f] or "").split(",") if code]
    return listing


# --- Moderation ---

async def pending_listing_ids(limit: int) -> list[int]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT id FROM listings WHERE status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        )
        return [row[0] for row in await cursor.fetchall()]


async def count_pending() -> int:
    async with _connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM listings WHERE status = 'pending'")
        return (await cursor.fetchone())[0]


async def decide_listing(
    listing_id: int, status: str, moderator_id: int, reason: str | None = None
) -> bool:
    """Approve or reject a pending listing. Returns False if it was already decided
    (e.g. the other admin was faster)."""
    async with _connect() as db:
        cursor = await db.execute(
            """
            UPDATE listings
            SET status = ?, moderator_id = ?, reject_reason = ?, updated_at = datetime('now')
            WHERE id = ? AND status = 'pending'
            """,
            (status, moderator_id, reason, listing_id),
        )
        await db.commit()
        return cursor.rowcount == 1


async def add_ticket_message(listing_id: int, admin_id: int, message_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO ticket_messages (listing_id, admin_id, message_id) "
            "VALUES (?, ?, ?)",
            (listing_id, admin_id, message_id),
        )
        await db.commit()


async def get_ticket_messages(listing_id: int) -> list[tuple[int, int]]:
    """(admin_id, message_id) pairs for all ticket copies of a listing."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT admin_id, message_id FROM ticket_messages WHERE listing_id = ?",
            (listing_id,),
        )
        return [(row[0], row[1]) for row in await cursor.fetchall()]


# --- Search ---

async def get_search_filters(telegram_id: int) -> dict:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT search_filters FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
    return json.loads(row[0]) if row and row[0] else {}


async def save_search_filters(telegram_id: int, filters: dict) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, search_filters) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET search_filters = excluded.search_filters
            """,
            (telegram_id, json.dumps(filters)),
        )
        await db.commit()


def _search_where(filters: dict) -> tuple[str, list]:
    """SQL WHERE clause and parameters for the given search filters."""
    where = ["status = 'approved'"]
    params: list = []

    term = filters.get("term")
    if term:
        where.append("',' || terms || ',' LIKE ?")
        params.append(f"%,{term},%")

    # Daily rentals are compared by price per day, everything else by price per month
    price_column = "price_day" if term == "day" else "price_month"
    if filters.get("price_min") is not None:
        where.append(f"{price_column} >= ?")
        params.append(filters["price_min"])
    if filters.get("price_max") is not None:
        where.append(f"{price_column} <= ?")
        params.append(filters["price_max"])

    for key, column in (("sectors", "sector"), ("rooms", "rooms")):
        values = filters.get(key) or []
        if values:
            where.append(f"{column} IN ({', '.join('?' for _ in values)})")
            params.extend(values)

    move_in = filters.get("move_in")
    if move_in:
        today = date.today().isoformat()
        # A date saved weeks ago may already be in the past
        day = today if move_in == "now" else max(move_in, today)
        where.append("(available_from IS NULL OR available_from <= ?)")
        params.append(day)

    if filters.get("autonomous"):
        where.append("heating = 'autonomous'")
    if filters.get("furnished"):
        where.append("furniture IN ('full', 'partial')")
    for code in ("pets", "kids"):
        if filters.get(code):
            where.append("',' || amenities || ',' LIKE ?")
            params.append(f"%,{code},%")

    return " AND ".join(where), params


async def search_count(filters: dict) -> int:
    where, params = _search_where(filters)
    async with _connect() as db:
        cursor = await db.execute(f"SELECT COUNT(*) FROM listings WHERE {where}", params)
        return (await cursor.fetchone())[0]


async def search_listing_id_at(filters: dict, offset: int) -> int | None:
    """ID of the listing at the given position in search results (newest first)."""
    where, params = _search_where(filters)
    async with _connect() as db:
        cursor = await db.execute(
            f"SELECT id FROM listings WHERE {where} "
            "ORDER BY updated_at DESC, id DESC LIMIT 1 OFFSET ?",
            (*params, offset),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


# --- Favorites ---

async def favorites_count(user_id: int) -> int:
    async with _connect() as db:
        cursor = await db.execute(
            """
            SELECT COUNT(*) FROM favorites f JOIN listings l ON l.id = f.listing_id
            WHERE f.user_id = ? AND l.status = 'approved'
            """,
            (user_id,),
        )
        return (await cursor.fetchone())[0]


async def favorite_listing_id_at(user_id: int, offset: int) -> int | None:
    async with _connect() as db:
        cursor = await db.execute(
            """
            SELECT l.id FROM favorites f JOIN listings l ON l.id = f.listing_id
            WHERE f.user_id = ? AND l.status = 'approved'
            ORDER BY f.created_at DESC, l.id DESC LIMIT 1 OFFSET ?
            """,
            (user_id, offset),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def is_favorite(user_id: int, listing_id: int) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND listing_id = ?",
            (user_id, listing_id),
        )
        return await cursor.fetchone() is not None


async def toggle_favorite(user_id: int, listing_id: int) -> bool:
    """Add to or remove from favorites. Returns True if it is a favorite now."""
    async with _connect() as db:
        cursor = await db.execute(
            "DELETE FROM favorites WHERE user_id = ? AND listing_id = ?",
            (user_id, listing_id),
        )
        if cursor.rowcount == 0:
            await db.execute(
                "INSERT INTO favorites (user_id, listing_id) VALUES (?, ?)",
                (user_id, listing_id),
            )
        await db.commit()
        return cursor.rowcount == 0


# --- Owner's listings ---

async def owner_listings(owner_id: int) -> list[dict]:
    """Short info about the owner's listings (without deleted), newest first."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, status, sector, rooms, price_month, price_day FROM listings
            WHERE owner_id = ? AND status != 'deleted' ORDER BY id DESC
            """,
            (owner_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def set_listing_status(
    listing_id: int, status: str, from_statuses: tuple[str, ...], edited: bool = False
) -> bool:
    """Change status only if the current one is in `from_statuses`.
    Returns False if the listing was in another status (e.g. changed meanwhile)."""
    extra = ", edited = 1, reject_reason = NULL" if edited else ""
    async with _connect() as db:
        cursor = await db.execute(
            f"""
            UPDATE listings SET status = ?, updated_at = datetime('now'){extra}
            WHERE id = ? AND status IN ({', '.join('?' for _ in from_statuses)})
            """,
            (status, listing_id, *from_statuses),
        )
        await db.commit()
        return cursor.rowcount == 1


async def update_listing(
    listing_id: int, fields: dict, photos: list[str] | None = None
) -> None:
    """Update the given listing fields and, if passed, replace all photos."""
    async with _connect() as db:
        if fields:
            assert set(fields) <= set(LISTING_FIELDS)
            values = [
                ",".join(value or []) if name in LIST_FIELDS else value
                for name, value in fields.items()
            ]
            await db.execute(
                f"UPDATE listings SET {', '.join(f'{name} = ?' for name in fields)} "
                "WHERE id = ?",
                (*values, listing_id),
            )
        if photos is not None:
            await db.execute("DELETE FROM listing_photos WHERE listing_id = ?", (listing_id,))
            await db.executemany(
                "INSERT INTO listing_photos (listing_id, position, file_id) VALUES (?, ?, ?)",
                [(listing_id, i, file_id) for i, file_id in enumerate(photos)],
            )
        await db.commit()


async def clear_ticket_messages(listing_id: int) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM ticket_messages WHERE listing_id = ?", (listing_id,))
        await db.commit()


# --- Subscriptions ---

def _filters_json(filters: dict) -> str:
    # sort_keys makes equal filters produce equal strings, so duplicates are detected
    return json.dumps(filters, sort_keys=True)


async def add_subscription(user_id: int, filters: dict) -> bool:
    """Returns False if the user already has a subscription with the same filters."""
    async with _connect() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO subscriptions (user_id, filters) VALUES (?, ?)",
            (user_id, _filters_json(filters)),
        )
        await db.commit()
        return cursor.rowcount == 1


async def user_subscriptions(user_id: int) -> list[tuple[int, dict]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT id, filters FROM subscriptions WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        return [(row[0], json.loads(row[1])) for row in await cursor.fetchall()]


async def get_subscription(subscription_id: int, user_id: int) -> dict | None:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT filters FROM subscriptions WHERE id = ? AND user_id = ?",
            (subscription_id, user_id),
        )
        row = await cursor.fetchone()
    return json.loads(row[0]) if row else None


async def delete_subscription(subscription_id: int, user_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "DELETE FROM subscriptions WHERE id = ? AND user_id = ?",
            (subscription_id, user_id),
        )
        await db.commit()


async def delete_user_subscriptions(user_id: int) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        await db.commit()


async def all_subscriptions() -> list[tuple[int, dict]]:
    """(user_id, filters) of every subscription."""
    async with _connect() as db:
        cursor = await db.execute("SELECT user_id, filters FROM subscriptions ORDER BY id")
        return [(row[0], json.loads(row[1])) for row in await cursor.fetchall()]


async def listing_matches(listing_id: int, filters: dict) -> bool:
    where, params = _search_where(filters)
    async with _connect() as db:
        cursor = await db.execute(
            f"SELECT 1 FROM listings WHERE id = ? AND {where}", (listing_id, *params)
        )
        return await cursor.fetchone() is not None


async def mark_notified(user_id: int, listing_id: int) -> bool:
    """Remember the notification. Returns False if this user was already notified."""
    async with _connect() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO sent_notifications (user_id, listing_id) VALUES (?, ?)",
            (user_id, listing_id),
        )
        await db.commit()
        return cursor.rowcount == 1
