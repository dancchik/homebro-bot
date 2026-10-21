import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import db
from bot.cards import render_listing
from bot.handlers.search import (
    CAPTION_DESCRIPTION_LIMIT,
    filter_rows,
    result_keyboard,
    send_main_view,
)
from bot.texts import all_variants, t

router = Router()

# Pause between notifications to stay well under Telegram's ~30 messages/second limit
SEND_DELAY = 0.05


class Sub(CallbackData, prefix="sub"):
    action: str  # show | delete
    subscription_id: int


# --- My subscriptions ---

def subscriptions_view(
    subscriptions: list[tuple[int, dict]], lang: str | None
) -> tuple[str, InlineKeyboardMarkup]:
    parts = [t(lang, "subs_title")]
    rows = []
    for n, (subscription_id, filters) in enumerate(subscriptions, start=1):
        lines = [
            f"{t(lang, key)}: {value}" for key, value in filter_rows(filters, lang) if value
        ]
        parts.append(t(lang, "sub_name").format(n=n) + "\n" + ("\n".join(lines) or t(lang, "sub_all")))
        rows.append([
            InlineKeyboardButton(
                text=t(lang, "btn_sub_show").format(n=n),
                callback_data=Sub(action="show", subscription_id=subscription_id).pack(),
            ),
            InlineKeyboardButton(
                text=t(lang, "btn_sub_delete").format(n=n),
                callback_data=Sub(action="delete", subscription_id=subscription_id).pack(),
            ),
        ])
    return "\n\n".join(parts), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.in_(all_variants("btn_subscriptions")))
async def open_subscriptions(message: Message, lang: str | None) -> None:
    subscriptions = await db.user_subscriptions(message.from_user.id)
    if not subscriptions:
        await message.answer(t(lang, "subs_empty"))
        return
    text, markup = subscriptions_view(subscriptions, lang)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Sub.filter())
async def subscription_button(
    callback: CallbackQuery, callback_data: Sub, lang: str | None
) -> None:
    user_id = callback.from_user.id
    filters = await db.get_subscription(callback_data.subscription_id, user_id)
    if filters is None:
        await callback.answer(t(lang, "outdated"))
        return

    if callback_data.action == "show":
        # Load the subscription into the search screen
        await db.save_search_filters(user_id, filters)
        await callback.answer()
        await send_main_view(callback.message, user_id, lang)
        return

    await db.delete_subscription(callback_data.subscription_id, user_id)
    await callback.answer(t(lang, "sub_deleted"))
    subscriptions = await db.user_subscriptions(user_id)
    if subscriptions:
        text, markup = subscriptions_view(subscriptions, lang)
        await callback.message.edit_text(text, reply_markup=markup)
    else:
        await callback.message.edit_text(t(lang, "subs_empty"))


# --- Notifications ---

async def notify_subscribers(bot: Bot, listing_id: int) -> None:
    """Tell everyone whose subscription matches this listing (once per user)."""
    listing = await db.get_listing(listing_id)
    if listing is None or listing["status"] != "approved":
        return
    notified = 0
    for user_id, filters in await db.all_subscriptions():
        if user_id == listing["owner_id"]:
            continue
        if not await db.listing_matches(listing_id, filters):
            continue
        if not await db.mark_notified(user_id, listing_id):
            continue  # already told about this listing (e.g. matched another subscription)
        lang = await db.get_lang(user_id)
        caption = (
            t(lang, "notify_title")
            + "\n\n"
            + render_listing(listing, lang, description_limit=CAPTION_DESCRIPTION_LIMIT)
            + f"\n\n<i>#{listing_id}</i>"
        )
        favorite = await db.is_favorite(user_id, listing_id)
        try:
            await bot.send_photo(
                user_id,
                listing["photos"][0],
                caption=caption,
                reply_markup=result_keyboard(listing, "n", 0, 1, favorite, lang),
            )
            notified += 1
        except TelegramForbiddenError:
            # The user blocked the bot — stop sending them anything
            await db.delete_user_subscriptions(user_id)
        except TelegramAPIError:
            logging.exception("Can't notify %s about listing #%s", user_id, listing_id)
        await asyncio.sleep(SEND_DELAY)
    logging.info("Listing #%s: notified %s subscribers", listing_id, notified)


_background_tasks: set[asyncio.Task] = set()


def notify_in_background(bot: Bot, listing_id: int) -> None:
    """Start notifications without making the admin wait for them."""
    task = asyncio.create_task(notify_subscribers(bot, listing_id))
    # Keep a reference so the task isn't garbage-collected before it finishes
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
