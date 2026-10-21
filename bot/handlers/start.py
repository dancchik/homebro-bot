from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import db
from bot.config import is_admin
from bot.keyboards import lang_keyboard, main_menu
from bot.texts import CHOOSE_LANG, LANGS, all_variants, t

router = Router()


async def send_welcome(message: Message, lang: str, user_id: int) -> None:
    await message.answer(
        t(lang, "welcome"), reply_markup=main_menu(lang, is_admin(user_id))
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, lang: str | None) -> None:
    await state.clear()
    if lang is None:
        await message.answer(CHOOSE_LANG, reply_markup=lang_keyboard())
        return
    await send_welcome(message, lang, message.from_user.id)


@router.message(F.text.in_(all_variants("btn_lang")))
async def change_lang(message: Message) -> None:
    await message.answer(CHOOSE_LANG, reply_markup=lang_keyboard())


@router.callback_query(F.data.startswith("lang:"))
async def lang_chosen(callback: CallbackQuery) -> None:
    lang = callback.data.split(":", 1)[1]
    if lang not in LANGS:
        await callback.answer()
        return
    await db.set_lang(callback.from_user.id, lang)
    await callback.message.edit_text(t(lang, "lang_saved"))
    await send_welcome(callback.message, lang, callback.from_user.id)
    await callback.answer()

