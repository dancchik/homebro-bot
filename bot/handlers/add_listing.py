from dataclasses import dataclass
from typing import Callable

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import db, options
from bot.cards import photo_album, render_listing
from bot.config import is_admin
from bot.handlers.moderation import notify_admins
from bot.keyboards import main_menu
from bot.parsing import parse_date, parse_floor, parse_number
from bot.texts import all_variants, t

router = Router()


class AddListing(StatesGroup):
    contact = State()
    filling = State()
    confirm = State()


class Wiz(CallbackData, prefix="w"):
    # pick | toggle | done | skip | now | back | cancel | submit | restart
    action: str
    value: str = ""


def _has_monthly(listing: dict) -> bool:
    return any(code != "day" for code in listing.get("terms", []))


def _has_daily(listing: dict) -> bool:
    return "day" in listing.get("terms", [])


@dataclass(frozen=True)
class Step:
    name: str
    kind: str  # choice | multi | number | text | floor | date | photos
    options: tuple[str, ...] = ()
    columns: int = 2
    optional: bool = False
    limits: tuple[int, int] = (0, 0)  # allowed range for numbers, max length for text
    when: Callable[[dict], bool] | None = None  # step is shown only if this returns True


STEPS = (
    Step("sector", "choice", options.SECTOR),
    Step("street", "text", optional=True, limits=(0, 100)),
    Step("rooms", "choice", options.ROOMS, columns=3),
    Step("area", "number", limits=(10, 500)),
    Step("floor", "floor"),
    Step("building", "choice", options.BUILDING),
    Step("heating", "choice", options.HEATING, columns=1),
    Step("renovation", "choice", options.RENOVATION),
    Step("furniture", "choice", options.FURNITURE, columns=1),
    Step("amenities", "multi", options.AMENITIES, optional=True),
    Step("terms", "multi", options.TERMS),
    Step("price_month", "number", limits=(50, 10000), when=_has_monthly),
    Step("price_day", "number", limits=(5, 500), when=_has_daily),
    Step("deposit", "number", optional=True, limits=(0, 10000)),
    Step("utilities", "choice", options.UTILITIES, columns=1),
    Step("available_from", "date"),
    Step("photos", "photos"),
    Step("description", "text", optional=True, limits=(0, 1000)),
)


def _applies(step: Step, listing: dict) -> bool:
    return step.when is None or step.when(listing)


# --- Keyboards and prompts ---

def _button(text: str, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=Wiz(action=action, value=value).pack()
    )


def contact_keyboard(lang: str | None) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "btn_share_contact"), request_contact=True)],
            [KeyboardButton(text=t(lang, "btn_cancel"))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def step_keyboard(
    step: Step, listing: dict, lang: str | None, can_go_back: bool
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if step.kind == "choice":
        for code in step.options:
            kb.add(_button(t(lang, f"{step.name}_{code}"), "pick", code))
        kb.adjust(step.columns)
    elif step.kind == "multi":
        selected = listing.get(step.name, [])
        for code in step.options:
            mark = "✅" if code in selected else "▫️"
            kb.add(_button(f"{mark} {t(lang, f'{step.name}_{code}')}", "toggle", code))
        kb.adjust(2)

    extra = []
    if step.kind in ("multi", "photos"):
        extra.append(_button(t(lang, "btn_done"), "done"))
    if step.kind == "date":
        extra.append(_button(t(lang, "btn_now"), "now"))
    if step.optional and step.kind in ("text", "number"):
        extra.append(_button(t(lang, "btn_skip"), "skip"))
    if extra:
        kb.row(*extra)

    nav = [_button(t(lang, "btn_back"), "back")] if can_go_back else []
    nav.append(_button(t(lang, "btn_cancel"), "cancel"))
    kb.row(*nav)
    return kb.as_markup()


def preview_keyboard(lang: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(t(lang, "btn_submit"), "submit")],
            [_button(t(lang, "btn_back"), "back"), _button(t(lang, "btn_restart"), "restart")],
            [_button(t(lang, "btn_cancel"), "cancel")],
        ]
    )


def prompt_text(
    step_index: int, listing: dict, lang: str | None, editing: bool = False
) -> str:
    step = STEPS[step_index]
    text = t(lang, f"q_{step.name}")
    if editing:
        if step.kind == "photos":
            text += "\n" + t(lang, "edit_photos_note")
    else:
        progress = t(lang, "step_progress").format(n=step_index + 1, total=len(STEPS))
        text = f"<i>{progress}</i>\n\n{text}"
    if step.kind == "photos" and listing.get("photos"):
        text += "\n\n" + t(lang, "photos_counter").format(n=len(listing["photos"]))
    return text


async def _remove_prompt_keyboard(message: Message, prompt_id: int | None) -> None:
    """Old prompts lose their buttons so they can't be pressed by mistake."""
    if not prompt_id:
        return
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=prompt_id, reply_markup=None
        )
    except TelegramBadRequest:
        pass


async def ask_current(message: Message, state: FSMContext, lang: str | None) -> None:
    data = await state.get_data()
    await _remove_prompt_keyboard(message, data.get("prompt_id"))
    step_index, listing = data["step"], data["listing"]
    sent = await message.answer(
        prompt_text(step_index, listing, lang, editing=bool(data.get("edit_id"))),
        reply_markup=step_keyboard(
            STEPS[step_index], listing, lang, can_go_back=bool(data["history"])
        ),
    )
    await state.update_data(prompt_id=sent.message_id)


async def start_filling(message: Message, state: FSMContext, lang: str | None) -> None:
    old_prompt_id = (await state.get_data()).get("prompt_id")
    await state.set_state(AddListing.filling)
    await state.set_data(
        {"step": 0, "history": [], "listing": {}, "prompt_id": old_prompt_id}
    )
    await ask_current(message, state, lang)


async def start_edit(
    message: Message, state: FSMContext, lang: str | None, listing: dict, field: str
) -> None:
    """Ask a single wizard step to change one field of an existing listing."""
    step_index = next(i for i, step in enumerate(STEPS) if step.name == field)
    values = {name: listing[name] for name in db.LISTING_FIELDS}
    # New photos replace the old set, so the photo step starts empty
    values["photos"] = [] if field == "photos" else listing["photos"]
    await state.set_state(AddListing.filling)
    await state.set_data({
        "step": step_index, "history": [], "listing": values, "prompt_id": None,
        "edit_id": listing["id"], "queue": [], "changed": [],
    })
    await ask_current(message, state, lang)


async def _edit_next(
    message: Message, state: FSMContext, lang: str | None, listing: dict, data: dict
) -> None:
    step = STEPS[data["step"]]
    changed = data["changed"] + [step.name]
    queue = list(data["queue"])
    if step.name == "terms":
        # New terms may need a price that wasn't asked before (e.g. "daily" added)
        for i, other in enumerate(STEPS):
            if other.name in ("price_month", "price_day") and _applies(other, listing) \
                    and not listing.get(other.name) and i not in queue:
                queue.append(i)
    if queue:
        await state.update_data(listing=listing, step=queue.pop(0), queue=queue, changed=changed)
        await ask_current(message, state, lang)
        return

    # Prices that no longer fit the terms are removed
    for other in STEPS:
        if not _applies(other, listing) and listing.get(other.name) is not None:
            listing[other.name] = None
            changed.append(other.name)
    await _remove_prompt_keyboard(message, data.get("prompt_id"))
    await state.clear()
    # Imported here because my_listings imports this module
    from bot.handlers.my_listings import finish_edit
    await finish_edit(message, data["edit_id"], listing, changed, lang)


async def go_next(
    message: Message, state: FSMContext, lang: str | None, listing: dict
) -> None:
    data = await state.get_data()
    if data.get("edit_id"):
        await _edit_next(message, state, lang, listing, data)
        return
    current = data["step"]
    history = data["history"] + [current]
    next_index = next(
        (i for i in range(current + 1, len(STEPS)) if _applies(STEPS[i], listing)),
        None,
    )
    if next_index is None:
        await state.update_data(listing=listing, history=history)
        await show_preview(message, state, lang)
        return
    await state.update_data(listing=listing, step=next_index, history=history)
    await ask_current(message, state, lang)


async def show_preview(message: Message, state: FSMContext, lang: str | None) -> None:
    data = await state.get_data()
    await _remove_prompt_keyboard(message, data.get("prompt_id"))
    listing = data["listing"]
    # Drop answers to steps that no longer apply (e.g. daily price after
    # the owner went back and unticked "daily")
    for step in STEPS:
        if not _applies(step, listing):
            listing.pop(step.name, None)

    await state.set_state(AddListing.confirm)
    await message.answer_media_group(photo_album(listing["photos"]))
    sent = await message.answer(
        render_listing(listing, lang) + f"\n\n<i>{t(lang, 'preview')}</i>",
        reply_markup=preview_keyboard(lang),
    )
    await state.update_data(listing=listing, prompt_id=sent.message_id)


async def _current_data(
    callback: CallbackQuery, state: FSMContext, lang: str | None
) -> dict | None:
    """State data if the pressed button belongs to the latest prompt, else None."""
    data = await state.get_data()
    if callback.message is None or callback.message.message_id != data.get("prompt_id"):
        await callback.answer(t(lang, "outdated"))
        return None
    return data


# --- Entry and contact ---

@router.message(F.text.in_(all_variants("btn_add")))
async def add_listing(message: Message, state: FSMContext, lang: str | None) -> None:
    if await db.get_phone(message.from_user.id) is None:
        await state.set_state(AddListing.contact)
        await message.answer(t(lang, "ask_contact"), reply_markup=contact_keyboard(lang))
        return
    await start_filling(message, state, lang)


@router.message(AddListing.contact, F.contact)
async def got_contact(message: Message, state: FSMContext, lang: str | None) -> None:
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer(t(lang, "contact_not_yours"))
        return
    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await db.save_contact(
        message.from_user.id, phone, message.from_user.full_name, message.from_user.username
    )
    await message.answer(
        t(lang, "contact_saved"),
        reply_markup=main_menu(lang, is_admin(message.from_user.id)),
    )
    await start_filling(message, state, lang)


@router.message(AddListing.contact, F.text.in_(all_variants("btn_cancel")))
async def contact_cancel(message: Message, state: FSMContext, lang: str | None) -> None:
    await state.clear()
    await message.answer(
        t(lang, "cancelled"), reply_markup=main_menu(lang, is_admin(message.from_user.id))
    )


@router.message(AddListing.contact)
async def contact_other(message: Message, lang: str | None) -> None:
    await message.answer(t(lang, "contact_not_yours"), reply_markup=contact_keyboard(lang))


# --- Typed answers ---

@router.message(AddListing.filling)
async def filling_message(message: Message, state: FSMContext, lang: str | None) -> None:
    data = await state.get_data()
    step = STEPS[data["step"]]
    listing = data["listing"]
    text = (message.text or "").strip()

    if step.kind == "photos":
        await _add_photo(message, state, lang, data)
        return
    if step.kind in ("choice", "multi"):
        await message.answer(t(lang, "use_buttons"))
        return

    if step.kind == "number":
        value = parse_number(text)
        low, high = step.limits
        if value is None or not low <= value <= high:
            await message.answer(t(lang, "err_range").format(min=low, max=high))
            return
        listing[step.name] = value
    elif step.kind == "text":
        max_len = step.limits[1]
        if not text or len(text) > max_len:
            await message.answer(t(lang, "err_text").format(max=max_len))
            return
        listing[step.name] = text
    elif step.kind == "floor":
        parsed = parse_floor(text)
        if parsed is None:
            await message.answer(t(lang, "err_floor"))
            return
        listing["floor"], listing["floors_total"] = parsed
    elif step.kind == "date":
        parsed = parse_date(text)
        if parsed is None:
            await message.answer(t(lang, "err_date"))
            return
        listing[step.name] = parsed.isoformat()

    await go_next(message, state, lang, listing)


async def _add_photo(
    message: Message, state: FSMContext, lang: str | None, data: dict
) -> None:
    if not message.photo:
        await message.answer(t(lang, "err_photo"))
        return
    listing = data["listing"]
    photos = listing.setdefault("photos", [])
    if len(photos) >= options.MAX_PHOTOS:
        await message.answer(t(lang, "err_photos_max"))
        return
    photos.append(message.photo[-1].file_id)  # last = highest resolution
    await state.update_data(listing=listing)

    # Re-send the prompt below the new photos so the counter and "Done" stay visible
    try:
        await message.bot.delete_message(message.chat.id, data["prompt_id"])
    except TelegramBadRequest:
        pass
    await ask_current(message, state, lang)


# --- Buttons ---

@router.callback_query(
    AddListing.filling,
    Wiz.filter(F.action.in_({"pick", "toggle", "done", "skip", "now"})),
)
async def step_button(
    callback: CallbackQuery, callback_data: Wiz, state: FSMContext, lang: str | None
) -> None:
    data = await _current_data(callback, state, lang)
    if data is None:
        return
    step = STEPS[data["step"]]
    listing = data["listing"]
    action, value = callback_data.action, callback_data.value

    if action == "pick" and step.kind == "choice" and value in step.options:
        listing[step.name] = value
    elif action == "toggle" and step.kind == "multi" and value in step.options:
        selected = set(listing.get(step.name, [])) ^ {value}
        listing[step.name] = [code for code in step.options if code in selected]
        await state.update_data(listing=listing)
        await callback.message.edit_reply_markup(
            reply_markup=step_keyboard(step, listing, lang, bool(data["history"]))
        )
        await callback.answer()
        return
    elif action == "done" and step.kind == "multi":
        if not step.optional and not listing.get(step.name):
            await callback.answer(t(lang, "err_select_one"), show_alert=True)
            return
        listing.setdefault(step.name, [])
    elif action == "done" and step.kind == "photos":
        if len(listing.get("photos", [])) < options.MIN_PHOTOS:
            await callback.answer(t(lang, "err_photos_min"), show_alert=True)
            return
    elif action == "skip" and step.optional:
        listing[step.name] = None
    elif action == "now" and step.kind == "date":
        listing[step.name] = None
    else:
        await callback.answer(t(lang, "outdated"))
        return

    await callback.answer()
    await go_next(callback.message, state, lang, listing)


@router.callback_query(
    StateFilter(AddListing.filling, AddListing.confirm), Wiz.filter(F.action == "back")
)
async def step_back(callback: CallbackQuery, state: FSMContext, lang: str | None) -> None:
    data = await _current_data(callback, state, lang)
    if data is None:
        return
    history = data["history"]
    if not history:
        await callback.answer()
        return
    previous = history.pop()
    await state.set_state(AddListing.filling)
    await state.update_data(step=previous, history=history)
    await callback.answer()
    await ask_current(callback.message, state, lang)


@router.callback_query(
    StateFilter(AddListing.filling, AddListing.confirm), Wiz.filter(F.action == "cancel")
)
async def wizard_cancel(callback: CallbackQuery, state: FSMContext, lang: str | None) -> None:
    data = await _current_data(callback, state, lang)
    if data is None:
        return
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t(lang, "edit_cancelled" if data.get("edit_id") else "cancelled"))
    await callback.answer()


@router.callback_query(AddListing.confirm, Wiz.filter(F.action == "restart"))
async def wizard_restart(callback: CallbackQuery, state: FSMContext, lang: str | None) -> None:
    if await _current_data(callback, state, lang) is None:
        return
    await callback.answer()
    await start_filling(callback.message, state, lang)


@router.callback_query(AddListing.confirm, Wiz.filter(F.action == "submit"))
async def wizard_submit(callback: CallbackQuery, state: FSMContext, lang: str | None) -> None:
    data = await _current_data(callback, state, lang)
    if data is None:
        return
    listing = data["listing"]
    listing_id = await db.create_listing(callback.from_user.id, listing, listing["photos"])
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t(lang, "submitted"))
    await callback.answer()
    await notify_admins(callback.bot, listing_id)


@router.callback_query(Wiz.filter())
async def outdated_button(callback: CallbackQuery, lang: str | None) -> None:
    await callback.answer(t(lang, "outdated"))
