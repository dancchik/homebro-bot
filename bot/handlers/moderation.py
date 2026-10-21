import logging
from html import escape
from typing import Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import db
from bot.cards import photo_album, render_listing
from bot.config import ADMIN_IDS, is_admin
from bot.handlers.subscriptions import notify_in_background
from bot.texts import MENU_TEXTS, all_variants, t

router = Router()

QUEUE_PAGE_SIZE = 10


class Mod(CallbackData, prefix="mod"):
    action: str  # approve | reject | cancel_reject
    listing_id: int


class Moderation(StatesGroup):
    reject_reason = State()


# --- Ticket rendering and sending ---

def ticket_text(listing: dict, lang: str | None) -> str:
    owner = [escape(listing["owner_name"] or "—")]
    if listing["owner_username"]:
        owner.append(f"@{listing['owner_username']}")
    if listing["owner_phone"]:
        owner.append(listing["owner_phone"])
    owner.append(f'<a href="tg://user?id={listing["owner_id"]}">{t(lang, "profile_link")}</a>')
    title_key = "ticket_title_edited" if listing.get("edited") else "ticket_title"
    return (
        t(lang, title_key).format(id=listing["id"])
        + "\n"
        + t(lang, "ticket_owner").format(owner=" · ".join(owner))
        + "\n\n"
        + render_listing(listing, lang)
    )


def ticket_keyboard(listing_id: int, lang: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=t(lang, "btn_approve"),
                callback_data=Mod(action="approve", listing_id=listing_id).pack(),
            ),
            InlineKeyboardButton(
                text=t(lang, "btn_reject"),
                callback_data=Mod(action="reject", listing_id=listing_id).pack(),
            ),
        ]]
    )


async def send_ticket(bot: Bot, admin_id: int, listing: dict) -> None:
    lang = await db.get_lang(admin_id)
    await bot.send_media_group(admin_id, photo_album(listing["photos"]))
    sent = await bot.send_message(
        admin_id, ticket_text(listing, lang), reply_markup=ticket_keyboard(listing["id"], lang)
    )
    await db.add_ticket_message(listing["id"], admin_id, sent.message_id)


async def notify_admins(bot: Bot, listing_id: int) -> None:
    """Send a new listing to every admin for review."""
    listing = await db.get_listing(listing_id)
    for admin_id in ADMIN_IDS:
        try:
            await send_ticket(bot, admin_id, listing)
        except TelegramAPIError:
            # Usually the admin hasn't pressed /start in the bot yet
            logging.exception("Can't send ticket #%s to admin %s", listing_id, admin_id)


async def close_ticket(
    bot: Bot, listing: dict, result: Callable[[str | None], str]
) -> None:
    """Replace buttons on every admin's copy of the ticket with the decision."""
    for admin_id, message_id in await db.get_ticket_messages(listing["id"]):
        lang = await db.get_lang(admin_id)
        try:
            await bot.edit_message_text(
                text=ticket_text(listing, lang) + "\n\n" + result(lang),
                chat_id=admin_id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramBadRequest:
            pass
    # Closed copies are final; a later ticket for the same listing starts fresh
    await db.clear_ticket_messages(listing["id"])


async def notify_owner(bot: Bot, listing: dict, key: str, **params: object) -> None:
    try:
        await bot.send_message(
            listing["owner_id"], t(listing["owner_lang"], key).format(**params)
        )
    except TelegramAPIError:
        logging.exception("Can't notify owner of listing #%s", listing["id"])


# --- Handlers ---

@router.message(F.text.in_(all_variants("btn_moderation")))
async def show_queue(message: Message, bot: Bot, lang: str | None) -> None:
    if not is_admin(message.from_user.id):
        return
    listing_ids = await db.pending_listing_ids(QUEUE_PAGE_SIZE)
    if not listing_ids:
        await message.answer(t(lang, "queue_empty"))
        return
    total = await db.count_pending()
    await message.answer(t(lang, "queue_count").format(n=total, shown=len(listing_ids)))
    for listing_id in listing_ids:
        await send_ticket(bot, message.from_user.id, await db.get_listing(listing_id))


@router.callback_query(Mod.filter(F.action == "approve"))
async def approve(
    callback: CallbackQuery, callback_data: Mod, bot: Bot, lang: str | None
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    listing_id = callback_data.listing_id
    if not await db.decide_listing(listing_id, "approved", callback.from_user.id):
        await callback.answer(t(lang, "already_processed"), show_alert=True)
        return
    await callback.answer()
    listing = await db.get_listing(listing_id)
    admin = escape(callback.from_user.full_name)
    await close_ticket(bot, listing, lambda l: t(l, "ticket_approved").format(admin=admin))
    await notify_owner(bot, listing, "owner_approved", id=listing_id)
    notify_in_background(bot, listing_id)


@router.callback_query(Mod.filter(F.action == "reject"))
async def reject(
    callback: CallbackQuery, callback_data: Mod, state: FSMContext, lang: str | None
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    listing_id = callback_data.listing_id
    listing = await db.get_listing(listing_id)
    if listing is None or listing["status"] != "pending":
        await callback.answer(t(lang, "already_processed"), show_alert=True)
        return
    await state.set_state(Moderation.reject_reason)
    await state.update_data(listing_id=listing_id)
    await callback.message.answer(
        t(lang, "ask_reject_reason").format(id=listing_id),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=t(lang, "btn_cancel"),
                callback_data=Mod(action="cancel_reject", listing_id=listing_id).pack(),
            )
        ]]),
    )
    await callback.answer()


@router.callback_query(Mod.filter(F.action == "cancel_reject"))
async def cancel_reject(callback: CallbackQuery, state: FSMContext, lang: str | None) -> None:
    await state.clear()
    await callback.message.edit_text(t(lang, "reject_cancelled"))
    await callback.answer()


@router.message(Moderation.reject_reason, F.text, ~F.text.in_(MENU_TEXTS))
async def got_reject_reason(
    message: Message, state: FSMContext, bot: Bot, lang: str | None
) -> None:
    listing_id = (await state.get_data())["listing_id"]
    await state.clear()
    reason = message.text.strip()[:500]
    if not await db.decide_listing(listing_id, "rejected", message.from_user.id, reason):
        await message.answer(t(lang, "already_processed"))
        return
    listing = await db.get_listing(listing_id)
    admin = escape(message.from_user.full_name)
    await close_ticket(
        bot,
        listing,
        lambda l: t(l, "ticket_rejected").format(admin=admin, reason=escape(reason)),
    )
    await notify_owner(bot, listing, "owner_rejected", id=listing_id, reason=escape(reason))
    await message.answer(t(lang, "reject_done"))
