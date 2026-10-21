from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import db
from bot.cards import render_listing
from bot.handlers.add_listing import start_edit
from bot.handlers.moderation import close_ticket, notify_admins
from bot.handlers.subscriptions import notify_in_background
from bot.texts import all_variants, t

router = Router()

EDITABLE = ("pending", "approved", "rejected")
STATUS_ICONS = {"pending": "🟡", "approved": "🟢", "rejected": "🔴", "rented": "⚪"}

# Changes to these fields go live right away; any other change needs a new review
QUICK_FIELDS = ("price_month", "price_day", "deposit", "available_from", "terms", "utilities")
REVIEW_FIELDS = (
    "sector", "street", "rooms", "area", "floor", "building", "heating",
    "renovation", "furniture", "amenities", "photos", "description",
)

CARD_DESCRIPTION_LIMIT = 200


class My(CallbackData, prefix="my"):
    # list | open | card | edit | field | rented | available | delete | delete_yes | resubmit
    action: str
    listing_id: int = 0
    value: str = ""


def _my(text: str, action: str, listing_id: int = 0, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=My(action=action, listing_id=listing_id, value=value).pack(),
    )


# --- Views ---

def list_view(items: list[dict], lang: str | None) -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    for item in items:
        if item["price_month"]:
            price = t(lang, "card_per_month").format(price=item["price_month"])
        else:
            price = t(lang, "card_per_day").format(price=item["price_day"])
        label = (
            f"{STATUS_ICONS[item['status']]} #{item['id']} · "
            f"{t(lang, 'rooms_' + item['rooms'])}, {t(lang, 'sector_' + item['sector'])} · {price}"
        )
        rows.append([_my(label, "open", item["id"])])
    return t(lang, "my_title"), InlineKeyboardMarkup(inline_keyboard=rows)


def card_caption(listing: dict, lang: str | None) -> str:
    lines = [t(lang, "my_status").format(status=t(lang, f"status_{listing['status']}"))]
    if listing["status"] == "rejected" and listing["reject_reason"]:
        lines.append(t(lang, "my_reason").format(reason=escape(listing["reject_reason"][:200])))
    return (
        "\n".join(lines)
        + "\n\n"
        + render_listing(listing, lang, description_limit=CARD_DESCRIPTION_LIMIT)
        + f"\n\n<i>#{listing['id']}</i>"
    )


def card_keyboard(listing: dict, lang: str | None) -> InlineKeyboardMarkup:
    listing_id, status = listing["id"], listing["status"]
    first = []
    if status in EDITABLE:
        first.append(_my(t(lang, "btn_edit"), "edit", listing_id))
    if status == "approved":
        first.append(_my(t(lang, "btn_rented"), "rented", listing_id))
    if status == "rejected":
        first.append(_my(t(lang, "btn_resubmit"), "resubmit", listing_id))
    if status == "rented":
        first.append(_my(t(lang, "btn_available"), "available", listing_id))
    return InlineKeyboardMarkup(inline_keyboard=[
        first,
        [_my(t(lang, "btn_delete"), "delete", listing_id)],
        [_my(t(lang, "btn_to_list"), "list")],
    ])


def editable_fields(listing: dict) -> list[str]:
    """Fields the owner can edit; price fields only if they fit the rental terms."""
    terms = listing["terms"]
    has_monthly = any(code != "day" for code in terms)
    fields = []
    for name in QUICK_FIELDS + REVIEW_FIELDS:
        if name == "price_month" and not has_monthly:
            continue
        if name == "price_day" and "day" not in terms:
            continue
        fields.append(name)
    return fields


def edit_keyboard(listing: dict, lang: str | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for name in editable_fields(listing):
        icon = "⚡" if name in QUICK_FIELDS else "🔍"
        kb.add(_my(f"{icon} {t(lang, f'field_{name}')}", "field", listing["id"], name))
    kb.adjust(2)
    kb.row(_my(t(lang, "btn_back"), "card", listing["id"]))
    return kb.as_markup()


async def send_list(message: Message, user_id: int, lang: str | None) -> None:
    items = await db.owner_listings(user_id)
    if not items:
        await message.answer(t(lang, "my_empty"))
        return
    text, markup = list_view(items, lang)
    await message.answer(text, reply_markup=markup)


async def send_card(message: Message, listing_id: int, lang: str | None) -> None:
    listing = await db.get_listing(listing_id)
    await message.answer_photo(
        listing["photos"][0],
        caption=card_caption(listing, lang),
        reply_markup=card_keyboard(listing, lang),
    )


async def _refresh_card(
    message: Message, listing: dict, lang: str | None, prefix: str = "",
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_caption(
            caption=prefix + card_caption(listing, lang),
            reply_markup=markup or card_keyboard(listing, lang),
        )
    except TelegramBadRequest:
        pass  # nothing changed


async def _delete(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def _own_listing(callback: CallbackQuery, listing_id: int, lang: str | None) -> dict | None:
    listing = await db.get_listing(listing_id)
    if (
        listing is None
        or listing["owner_id"] != callback.from_user.id
        or listing["status"] == "deleted"
    ):
        await callback.answer(t(lang, "listing_unavailable"), show_alert=True)
        return None
    return listing


# --- Handlers ---

@router.message(F.text.in_(all_variants("btn_my")))
async def open_my_listings(message: Message, lang: str | None) -> None:
    await send_list(message, message.from_user.id, lang)


@router.callback_query(My.filter(F.action == "list"))
async def back_to_list(callback: CallbackQuery, lang: str | None) -> None:
    await callback.answer()
    await _delete(callback.message)
    await send_list(callback.message, callback.from_user.id, lang)


@router.callback_query(My.filter())
async def listing_button(
    callback: CallbackQuery, callback_data: My, state: FSMContext, lang: str | None
) -> None:
    listing = await _own_listing(callback, callback_data.listing_id, lang)
    if listing is None:
        return
    listing_id, action = listing["id"], callback_data.action
    bot = callback.bot

    if action == "open":
        await callback.answer()
        await _delete(callback.message)
        await send_card(callback.message, listing_id, lang)
    elif action == "card":
        await callback.answer()
        await _refresh_card(callback.message, listing, lang)
    elif action == "edit" and listing["status"] in EDITABLE:
        await callback.answer()
        await _refresh_card(
            callback.message, listing, lang,
            prefix=t(lang, "edit_choose") + "\n\n",
            markup=edit_keyboard(listing, lang),
        )
    elif action == "field" and listing["status"] in EDITABLE:
        if callback_data.value not in editable_fields(listing):
            await callback.answer(t(lang, "outdated"))
            return
        await callback.answer()
        await _refresh_card(callback.message, listing, lang)
        await start_edit(callback.message, state, lang, listing, callback_data.value)
    elif action == "rented" and await db.set_listing_status(listing_id, "rented", ("approved",)):
        await callback.answer(t(lang, "marked_rented"), show_alert=True)
        await _refresh_card(callback.message, await db.get_listing(listing_id), lang)
    elif action == "available" and await db.set_listing_status(listing_id, "approved", ("rented",)):
        await callback.answer(t(lang, "marked_available"), show_alert=True)
        await _refresh_card(callback.message, await db.get_listing(listing_id), lang)
        notify_in_background(bot, listing_id)
    elif action == "resubmit" and await db.set_listing_status(
        listing_id, "pending", ("rejected",), edited=True
    ):
        await callback.answer(t(lang, "resubmitted").format(id=listing_id), show_alert=True)
        await _refresh_card(callback.message, await db.get_listing(listing_id), lang)
        await notify_admins(bot, listing_id)
    elif action == "delete":
        await callback.answer()
        await _refresh_card(
            callback.message, listing, lang,
            prefix=t(lang, "delete_confirm").format(id=listing_id) + "\n\n",
            markup=InlineKeyboardMarkup(inline_keyboard=[[
                _my(t(lang, "btn_delete_yes"), "delete_yes", listing_id),
                _my(t(lang, "btn_back"), "card", listing_id),
            ]]),
        )
    elif action == "delete_yes" and await db.set_listing_status(
        listing_id, "deleted", ("pending", "approved", "rejected", "rented")
    ):
        await callback.answer()
        if listing["status"] == "pending":
            await close_ticket(bot, listing, lambda l: t(l, "ticket_deleted"))
        await _delete(callback.message)
        await callback.message.answer(t(lang, "deleted").format(id=listing_id))
        await send_list(callback.message, callback.from_user.id, lang)
    else:
        # Status changed meanwhile (e.g. a moderator decided) — show the current state
        await callback.answer(t(lang, "outdated"))
        await _refresh_card(callback.message, listing, lang)


async def finish_edit(
    message: Message, listing_id: int, listing: dict, changed: list[str], lang: str | None
) -> None:
    """Save fields edited in the wizard and apply moderation rules."""
    current = await db.get_listing(listing_id)
    if (
        current is None
        or current["owner_id"] != message.chat.id
        or current["status"] not in EDITABLE
    ):
        await message.answer(t(lang, "listing_unavailable"))
        return

    fields, photos = {}, None
    for name in set(changed):
        if name == "photos":
            photos = listing["photos"]
        elif name == "floor":
            fields["floor"], fields["floors_total"] = listing["floor"], listing["floors_total"]
        else:
            fields[name] = listing.get(name)
    await db.update_listing(listing_id, fields, photos)

    bot = message.bot
    needs_review = any(name not in QUICK_FIELDS for name in changed)
    status = current["status"]
    if status == "approved" and needs_review:
        await db.set_listing_status(listing_id, "pending", ("approved",), edited=True)
        await notify_admins(bot, listing_id)
        key = "edit_saved_review"
    elif status == "pending":
        # Admins must see the new version: close old tickets and send a fresh one
        await close_ticket(bot, await db.get_listing(listing_id), lambda l: t(l, "ticket_outdated"))
        await db.set_listing_status(listing_id, "pending", ("pending",), edited=True)
        await notify_admins(bot, listing_id)
        key = "edit_saved_pending"
    elif status == "rejected":
        key = "edit_saved_rejected"
    else:
        key = "edit_saved"
        # E.g. a lower price may now fit someone's subscription
        notify_in_background(bot, listing_id)
    await message.answer(t(lang, key))
    await send_card(message, listing_id, lang)
