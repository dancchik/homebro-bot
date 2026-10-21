from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.texts import LANGS, t


def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"lang:{code}")]
            for code, name in LANGS.items()
        ]
    )


def main_menu(lang: str, is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t(lang, "btn_search"))],
        [
            KeyboardButton(text=t(lang, "btn_favorites")),
            KeyboardButton(text=t(lang, "btn_subscriptions")),
        ],
        [
            KeyboardButton(text=t(lang, "btn_add")),
            KeyboardButton(text=t(lang, "btn_my")),
        ],
        [KeyboardButton(text=t(lang, "btn_lang"))],
    ]
    if is_admin:
        rows[-1].append(KeyboardButton(text=t(lang, "btn_moderation")))
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
