from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import db, options
from bot.cards import photo_album, render_listing
from bot.parsing import parse_date, parse_price_range
from bot.texts import MENU_TEXTS, all_variants, t

router = Router()

DEFAULT_FILTERS = {
    "term": None,  # one of options.TERMS or None
    "price_min": None,
    "price_max": None,
    "sectors": [],
    "rooms": [],
    "move_in": None,  # None = any, "now" or ISO date
    "autonomous": False,
    "furnished": False,
    "pets": False,
    "kids": False,
}
MORE_FLAGS = ("autonomous", "furnished", "pets", "kids")

# Multi-select filter sections: section -> (filter key, label prefix, options, columns)
MULTI_SECTIONS = {
    "sector": ("sectors", "sector", options.SECTOR, 2),
    "rooms": ("rooms", "rooms", options.ROOMS, 3),
}

PRICE_PRESETS = {
    "month": ((None, 250), (250, 350), (350, 500), (500, 700), (700, None)),
    "day": ((None, 25), (25, 40), (40, 60), (60, None)),
}

# Photo captions are limited to 1024 characters
CAPTION_DESCRIPTION_LIMIT = 300

MAX_SUBSCRIPTIONS = 5


class Flt(CallbackData, prefix="f"):
    # open | term | price | toggle | clear | move_in | back | reset | show
    # | custom_price | custom_date | screen | noop
    action: str
    value: str = ""


class Browse(CallbackData, prefix="br"):
    src: str  # "s" = search results, "f" = favorites, "n" = subscription notification
    pos: int


class ListingAction(CallbackData, prefix="la"):
    action: str  # photos | contact | fav
    listing_id: int
    src: str
    pos: int


class SearchInput(StatesGroup):
    price = State()
    move_in = State()


# --- Filters ---

async def load_filters(user_id: int) -> dict:
    return {**DEFAULT_FILTERS, **await db.get_search_filters(user_id)}


def _price_scale(filters: dict) -> str:
    return "day" if filters["term"] == "day" else "month"


def price_text(low: int | None, high: int | None, lang: str | None, scale: str) -> str:
    if low is None and high is None:
        return t(lang, "f_any")
    if low is None:
        text = t(lang, "price_upto").format(max=high)
    elif high is None:
        text = t(lang, "price_from").format(min=low)
    else:
        text = t(lang, "price_range").format(min=low, max=high)
    return f"{text} {t(lang, f'unit_{scale}')}"


def _encode_range(low: int | None, high: int | None) -> str:
    return f"{'' if low is None else low}-{'' if high is None else high}"


def _decode_range(value: str) -> tuple[int | None, int | None]:
    low, high = value.split("-")
    return (int(low) if low else None, int(high) if high else None)


def filter_rows(filters: dict, lang: str | None) -> list[tuple[str, str | None]]:
    """(label key, value text) for each filter; value is None when the filter isn't set."""
    filters = {**DEFAULT_FILTERS, **filters}

    def labels(prefix: str, codes: list[str]) -> str | None:
        return ", ".join(t(lang, f"{prefix}_{code}") for code in codes) or None

    move_in = filters["move_in"]
    if move_in is None:
        move_in_text = None
    elif move_in == "now":
        move_in_text = t(lang, "f_move_in_now")
    else:
        move_in_text = date.fromisoformat(move_in).strftime("%d.%m.%Y")

    has_price = filters["price_min"] is not None or filters["price_max"] is not None
    return [
        ("f_term", t(lang, f"terms_{filters['term']}") if filters["term"] else None),
        ("f_price", price_text(
            filters["price_min"], filters["price_max"], lang, _price_scale(filters)
        ) if has_price else None),
        ("f_sector", labels("sector", filters["sectors"])),
        ("f_rooms", labels("rooms", filters["rooms"])),
        ("f_move_in", move_in_text),
        ("f_more", ", ".join(t(lang, f"more_{k}") for k in MORE_FLAGS if filters[k]) or None),
    ]


def filters_text(filters: dict, lang: str | None) -> str:
    any_ = t(lang, "f_any")
    return t(lang, "search_title") + "\n\n" + "\n".join(
        f"{t(lang, key)}: <b>{value or any_}</b>" for key, value in filter_rows(filters, lang)
    )


def _flt(text: str, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=Flt(action=action, value=value).pack()
    )


def filters_keyboard(lang: str | None, count: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for section in ("term", "price", "sector", "rooms", "move_in", "more"):
        kb.add(_flt(t(lang, f"f_{section}"), "open", section))
    kb.adjust(2)
    kb.row(_flt(t(lang, "btn_reset"), "reset"))
    kb.row(_flt(t(lang, "btn_subscribe"), "subscribe"))
    show = t(lang, "btn_show").format(n=count) if count else t(lang, "btn_nothing")
    kb.row(_flt(show, "show"))
    return kb.as_markup()


def section_view(section: str, filters: dict, lang: str | None) -> tuple[str, InlineKeyboardMarkup]:
    """Text and keyboard of one filter's submenu."""
    kb = InlineKeyboardBuilder()

    def mark(selected: bool) -> str:
        return "✅ " if selected else ""

    if section == "term":
        text = t(lang, "q_f_term")
        for code in options.TERMS:
            kb.add(_flt(mark(filters["term"] == code) + t(lang, f"terms_{code}"), "term", code))
        kb.adjust(2)
        kb.row(_flt(t(lang, "btn_any"), "term"))
    elif section == "price":
        scale = _price_scale(filters)
        text = t(lang, f"q_f_price_{scale}")
        current = (filters["price_min"], filters["price_max"])
        for low, high in PRICE_PRESETS[scale]:
            kb.add(_flt(
                mark(current == (low, high)) + price_text(low, high, lang, scale),
                "price",
                _encode_range(low, high),
            ))
        kb.adjust(2)
        kb.row(
            _flt(t(lang, "btn_custom_price"), "custom_price"),
            _flt(t(lang, "btn_any"), "price", "-"),
        )
    elif section in MULTI_SECTIONS:
        key, prefix, codes, columns = MULTI_SECTIONS[section]
        text = t(lang, f"q_f_{section}")
        for code in codes:
            selected = code in filters[key]
            kb.add(_flt(
                ("✅ " if selected else "▫️ ") + t(lang, f"{prefix}_{code}"),
                "toggle",
                f"{key}.{code}",
            ))
        kb.adjust(columns)
        kb.row(_flt(t(lang, "btn_any"), "clear", key), _flt(t(lang, "btn_done"), "back"))
    elif section == "move_in":
        text = t(lang, "q_f_move_in")
        kb.row(_flt(mark(filters["move_in"] == "now") + t(lang, "btn_now"), "move_in", "now"))
        kb.row(_flt(t(lang, "btn_custom_date"), "custom_date"))
        kb.row(_flt(t(lang, "btn_any"), "move_in"))
    else:  # "more"
        text = t(lang, "q_f_more")
        for flag in MORE_FLAGS:
            kb.row(_flt(
                ("✅ " if filters[flag] else "▫️ ") + t(lang, f"more_{flag}"),
                "toggle",
                f"more.{flag}",
            ))
        kb.row(_flt(t(lang, "btn_done"), "back"))

    if section in ("term", "price", "move_in"):
        kb.row(_flt(t(lang, "btn_back"), "back"))
    return text, kb.as_markup()


async def main_view(user_id: int, lang: str | None) -> tuple[str, InlineKeyboardMarkup]:
    filters = await load_filters(user_id)
    count = await db.search_count(filters)
    return filters_text(filters, lang), filters_keyboard(lang, count)


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # "message is not modified" when the same button is pressed twice


async def send_main_view(message: Message, user_id: int, lang: str | None) -> None:
    text, markup = await main_view(user_id, lang)
    await message.answer(text, reply_markup=markup)


@router.message(F.text.in_(all_variants("btn_search")))
async def open_search(message: Message, state: FSMContext, lang: str | None) -> None:
    if await state.get_state() in (SearchInput.price.state, SearchInput.move_in.state):
        await state.clear()
    await send_main_view(message, message.from_user.id, lang)


@router.callback_query(Flt.filter())
async def filter_button(
    callback: CallbackQuery, callback_data: Flt, state: FSMContext, lang: str | None
) -> None:
    user_id = callback.from_user.id
    action, value = callback_data.action, callback_data.value
    filters = await load_filters(user_id)

    if action == "noop":
        await callback.answer()
        return
    if action == "open":
        await _edit(callback.message, *section_view(value, filters, lang))
        await callback.answer()
        return
    if action == "custom_price":
        await state.set_state(SearchInput.price)
        await callback.message.answer(t(lang, "ask_custom_price"))
        await callback.answer()
        return
    if action == "custom_date":
        await state.set_state(SearchInput.move_in)
        await callback.message.answer(t(lang, "ask_custom_date"))
        await callback.answer()
        return
    if action == "screen":
        await send_main_view(callback.message, user_id, lang)
        await callback.answer()
        return
    if action == "show":
        await callback.answer()
        await show_result(callback.message, user_id, lang, "s", 0, edit=False)
        return
    if action == "subscribe":
        if len(await db.user_subscriptions(user_id)) >= MAX_SUBSCRIPTIONS:
            text = t(lang, "subs_limit").format(n=MAX_SUBSCRIPTIONS)
        elif await db.add_subscription(user_id, filters):
            text = t(lang, "subscribed")
        else:
            text = t(lang, "already_subscribed")
        await callback.answer(text, show_alert=True)
        return

    stay_in_section = None
    if action == "term":
        new_term = value or None
        old_scale = _price_scale(filters)
        filters["term"] = new_term
        if _price_scale(filters) != old_scale:
            # Monthly and daily budgets aren't comparable
            filters["price_min"] = filters["price_max"] = None
    elif action == "price":
        filters["price_min"], filters["price_max"] = _decode_range(value)
    elif action == "toggle":
        key, code = value.split(".", 1)
        if key == "more":
            filters[code] = not filters[code]
            stay_in_section = "more"
        else:
            selected = set(filters[key]) ^ {code}
            section = next(s for s, spec in MULTI_SECTIONS.items() if spec[0] == key)
            filters[key] = [c for c in MULTI_SECTIONS[section][2] if c in selected]
            stay_in_section = section
    elif action == "clear":
        filters[value] = []
    elif action == "move_in":
        filters["move_in"] = value or None
    elif action == "reset":
        filters = dict(DEFAULT_FILTERS)
    # "back" just returns to the main screen

    await db.save_search_filters(user_id, filters)
    if stay_in_section:
        await _edit(callback.message, *section_view(stay_in_section, filters, lang))
    else:
        await _edit(callback.message, *await main_view(user_id, lang))
    await callback.answer()


@router.message(SearchInput.price, ~F.text.in_(MENU_TEXTS))
async def custom_price_input(message: Message, state: FSMContext, lang: str | None) -> None:
    parsed = parse_price_range(message.text or "")
    if parsed is None:
        await message.answer(t(lang, "err_custom_price"))
        return
    filters = await load_filters(message.from_user.id)
    filters["price_min"], filters["price_max"] = parsed
    await db.save_search_filters(message.from_user.id, filters)
    await state.clear()
    await send_main_view(message, message.from_user.id, lang)


@router.message(SearchInput.move_in, ~F.text.in_(MENU_TEXTS))
async def custom_date_input(message: Message, state: FSMContext, lang: str | None) -> None:
    parsed = parse_date(message.text or "")
    if parsed is None:
        await message.answer(t(lang, "err_date"))
        return
    filters = await load_filters(message.from_user.id)
    filters["move_in"] = parsed.isoformat()
    await db.save_search_filters(message.from_user.id, filters)
    await state.clear()
    await send_main_view(message, message.from_user.id, lang)


# --- Results and favorites viewer ---

async def _result_at(user_id: int, src: str, pos: int) -> tuple[dict | None, int, int]:
    """(listing, total, normalized position) for search results or favorites."""
    if src == "f":
        total = await db.favorites_count(user_id)
    else:
        filters = await load_filters(user_id)
        total = await db.search_count(filters)
    if total == 0:
        return None, 0, 0
    pos %= total  # wrap around: "next" on the last card goes to the first
    if src == "f":
        listing_id = await db.favorite_listing_id_at(user_id, pos)
    else:
        listing_id = await db.search_listing_id_at(filters, pos)
    return await db.get_listing(listing_id), total, pos


def result_keyboard(
    listing: dict, src: str, pos: int, total: int, favorite: bool, lang: str | None
) -> InlineKeyboardMarkup:
    def act(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text,
            callback_data=ListingAction(
                action=action, listing_id=listing["id"], src=src, pos=pos
            ).pack(),
        )

    def nav(text: str, new_pos: int) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text, callback_data=Browse(src=src, pos=new_pos).pack()
        )

    rows = []
    if total > 1:
        rows.append([
            nav("◀️", pos - 1),
            _flt(f"{pos + 1} / {total}", "noop"),
            nav("▶️", pos + 1),
        ])
    rows.append([
        act(t(lang, "btn_contact"), "contact"),
        act(t(lang, "btn_fav_remove" if favorite else "btn_fav_add"), "fav"),
    ])
    if len(listing["photos"]) > 1:
        rows.append([act(t(lang, "btn_all_photos").format(n=len(listing["photos"])), "photos")])
    if src == "s":
        rows.append([_flt(t(lang, "btn_filters"), "screen")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_result(
    message: Message, user_id: int, lang: str | None, src: str, pos: int, edit: bool
) -> None:
    listing, total, pos = await _result_at(user_id, src, pos)
    if listing is None:
        if src == "f":
            await message.answer(t(lang, "favorites_empty"))
        else:
            # Nothing yet — the best moment to offer a subscription
            await message.answer(
                t(lang, "nothing_found"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [_flt(t(lang, "btn_subscribe"), "subscribe")],
                    [_flt(t(lang, "btn_filters"), "screen")],
                ]),
            )
        return
    caption = (
        render_listing(listing, lang, description_limit=CAPTION_DESCRIPTION_LIMIT)
        + f"\n\n<i>#{listing['id']}</i>"
    )
    markup = result_keyboard(
        listing, src, pos, total, await db.is_favorite(user_id, listing["id"]), lang
    )
    if edit:
        try:
            await message.edit_media(
                InputMediaPhoto(media=listing["photos"][0], caption=caption),
                reply_markup=markup,
            )
        except TelegramBadRequest:
            pass  # same listing shown again (only one result)
    else:
        await message.answer_photo(listing["photos"][0], caption=caption, reply_markup=markup)


@router.message(F.text.in_(all_variants("btn_favorites")))
async def open_favorites(message: Message, lang: str | None) -> None:
    await show_result(message, message.from_user.id, lang, "f", 0, edit=False)


@router.callback_query(Browse.filter())
async def browse(callback: CallbackQuery, callback_data: Browse, lang: str | None) -> None:
    await callback.answer()
    await show_result(
        callback.message, callback.from_user.id, lang,
        callback_data.src, callback_data.pos, edit=True,
    )


@router.callback_query(ListingAction.filter())
async def listing_action(
    callback: CallbackQuery, callback_data: ListingAction, lang: str | None
) -> None:
    listing = await db.get_listing(callback_data.listing_id)
    if listing is None or listing["status"] != "approved":
        await callback.answer(t(lang, "listing_unavailable"), show_alert=True)
        return

    if callback_data.action == "photos":
        await callback.answer()
        await callback.message.answer_media_group(photo_album(listing["photos"]))
    elif callback_data.action == "contact":
        await callback.answer()
        await callback.message.answer_contact(
            phone_number=listing["owner_phone"], first_name=listing["owner_name"] or "—"
        )
        telegram = (
            t(lang, "contact_tg").format(username=listing["owner_username"])
            if listing["owner_username"]
            else ""
        )
        await callback.message.answer(
            t(lang, "contact_tip").format(id=listing["id"], telegram=telegram)
        )
    elif callback_data.action == "fav":
        now_favorite = await db.toggle_favorite(callback.from_user.id, listing["id"])
        await callback.answer(t(lang, "fav_added" if now_favorite else "fav_removed"))
        if callback_data.src == "n":
            # A subscription notification: just update its button
            await callback.message.edit_reply_markup(
                reply_markup=result_keyboard(listing, "n", 0, 1, now_favorite, lang)
            )
            return
        # Re-render: in favorites a removed card is replaced by the next one
        await show_result(
            callback.message, callback.from_user.id, lang,
            callback_data.src, callback_data.pos, edit=True,
        )
