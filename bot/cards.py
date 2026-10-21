from datetime import date
from html import escape

from aiogram.types import InputMediaPhoto

from bot.texts import t


def _label(lang: str, field: str, code: str) -> str:
    return t(lang, f"{field}_{code}")


def render_listing(
    listing: dict, lang: str | None, description_limit: int | None = None
) -> str:
    """Listing card text (HTML). `listing` may come from the wizard or from the DB.

    `description_limit` shortens the description, e.g. to fit a photo caption.
    """
    lines = [
        "🏠 <b>"
        + t(lang, "card_rooms_area").format(
            rooms=_label(lang, "rooms", listing["rooms"]), area=listing["area"]
        )
        + "</b> · "
        + _label(lang, "sector", listing["sector"])
    ]
    if listing.get("street"):
        lines.append(f"📍 {escape(listing['street'])}")

    prices = []
    if listing.get("price_month"):
        prices.append(t(lang, "card_per_month").format(price=listing["price_month"]))
    if listing.get("price_day"):
        prices.append(t(lang, "card_per_day").format(price=listing["price_day"]))
    if listing.get("deposit"):
        prices.append(t(lang, "card_deposit").format(price=listing["deposit"]))
    else:
        prices.append(t(lang, "card_no_deposit"))
    lines.append("💶 " + " · ".join(prices))
    lines.append("💡 " + _label(lang, "utilities", listing["utilities"]))

    available_from = listing.get("available_from")
    if available_from and date.fromisoformat(available_from) > date.today():
        available = t(lang, "card_available_from").format(
            date=date.fromisoformat(available_from).strftime("%d.%m.%Y")
        )
    else:
        available = t(lang, "card_available_now")
    terms = ", ".join(_label(lang, "terms", code) for code in listing["terms"])
    lines.append(f"🗓 {available} · " + t(lang, "card_terms").format(terms=terms))

    floor = t(lang, "card_floor").format(
        floor=listing["floor"], floors=listing["floors_total"]
    )
    lines.append(f"🏢 {floor} · " + _label(lang, "building", listing["building"]))
    lines.append("🔥 " + _label(lang, "heating", listing["heating"]))
    lines.append(
        "🛋 "
        + _label(lang, "renovation", listing["renovation"])
        + " · "
        + _label(lang, "furniture", listing["furniture"])
    )
    if listing.get("amenities"):
        lines.append(
            "✅ " + " · ".join(_label(lang, "amenities", c) for c in listing["amenities"])
        )
    description = listing.get("description")
    if description:
        if description_limit and len(description) > description_limit:
            description = description[:description_limit].rstrip() + "…"
        lines.append("\n" + escape(description))
    return "\n".join(lines)


def photo_album(photos: list[str]) -> list[InputMediaPhoto]:
    return [InputMediaPhoto(media=file_id) for file_id in photos]
