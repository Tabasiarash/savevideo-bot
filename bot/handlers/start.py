"""/start, /help, /language and the inline deep-link entry point."""

from __future__ import annotations

import contextlib

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.deps import get_services
from bot.handlers.common import register_user, text
from bot.i18n import LANGUAGE_NAMES, get_text

router = Router()


def language_keyboard(lang: str) -> InlineKeyboardMarkup:
    from aiogram.types import InlineKeyboardButton

    rows = [
        [InlineKeyboardButton(text=LANGUAGE_NAMES[code], callback_data=f"lang:{code}")]
        for code in ("en", "de", "fa")
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, bot: Bot) -> None:
    srv = get_services()
    lang = await register_user(srv.db, message)
    user = message.from_user
    if user is None:
        return
    if await srv.db.is_banned(user.id):
        await message.answer(text(lang, "banned"))
        return
    payload = command.args or ""
    if payload.startswith("dl_"):
        from bot.handlers import links

        await links.handle_deeplink(message, bot, payload[3:])
        return
    await message.answer(text(lang, "start", name=user.full_name or user.first_name or user.username or ""))

    if payload.startswith("lang_"):
        code = payload[5:]
        if code in LANGUAGE_NAMES:
            await srv.db.set_language(user.id, code)
            await message.answer(text(code, "language_set", lang=LANGUAGE_NAMES[code]))


@router.message(Command("help"))
async def cmd_help(message: Message, bot: Bot) -> None:
    srv = get_services()
    lang = await register_user(srv.db, message)
    botname = ""
    with contextlib.suppress(Exception):
        botname = (await bot.me()).username or ""
    await message.answer(text(lang, "help", botname=botname))


@router.message(Command("language"))
async def cmd_language(message: Message) -> None:
    srv = get_services()
    lang = await register_user(srv.db, message)
    await message.answer(text(lang, "choose_language"), reply_markup=language_keyboard(lang))


@router.callback_query(F.data.startswith("lang:"))
async def on_language_pick(callback: CallbackQuery, bot: Bot) -> None:
    code = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    srv = get_services()
    if code not in LANGUAGE_NAMES:
        await callback.answer()
        return
    # The user may not exist yet when they only ever used inline mode.
    await srv.db.set_language(user_id, code)
    await callback.answer(get_text(code, "language_set", lang=LANGUAGE_NAMES[code]))
    with contextlib.suppress(Exception):  # callback message may be inline
        await callback.message.edit_text(get_text(code, "language_set", lang=LANGUAGE_NAMES[code]))
