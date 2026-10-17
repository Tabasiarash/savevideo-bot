"""/start, /help, /language and the inline deep-link entry point."""

from __future__ import annotations

import contextlib
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import SUBTITLE_MODES, settings
from bot.deps import get_services
from bot.handlers.common import register_user, text
from bot.i18n import LANGUAGE_NAMES, get_text

router = Router()


def language_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=LANGUAGE_NAMES[code], callback_data=f"lang:{code}")]
        for code in ("en", "de", "fa")
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mode_label(lang: str, mode: str) -> str:
    return text(lang, f"button_mode_{settings.normalize_subtitle_mode(mode)}")


def settings_keyboard(lang: str, selected: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if code == selected else "") + text(lang, f"button_mode_{code}"),
                callback_data=f"setsub:{code}",
            )
        ]
        for code in SUBTITLE_MODES
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


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    srv = get_services()
    lang = await register_user(srv.db, message)
    user = message.from_user
    if user is None:
        return
    if not settings.enable_subtitles:
        await message.answer(text(lang, "unknown_command"))
        return
    mode = settings.normalize_subtitle_mode(await srv.db.get_subtitle_mode(user.id))
    await message.answer(
        text(lang, "settings_title", mode=_mode_label(lang, mode)),
        reply_markup=settings_keyboard(lang, mode),
    )


@router.callback_query(F.data.startswith("setsub:"))
async def on_subtitle_mode_pick(callback: CallbackQuery) -> None:
    code = callback.data.split(":", 1)[1]
    if code not in SUBTITLE_MODES:
        await callback.answer()
        return
    srv = get_services()
    user_id = callback.from_user.id
    await srv.db.upsert_user(user_id, getattr(callback.from_user, "username", None), int(time.time()))
    lang = await srv.db.get_language(user_id) or settings.default_language
    await srv.db.set_subtitle_mode(user_id, code)
    selected = settings.normalize_subtitle_mode(code)
    await callback.answer(text(lang, "settings_saved", mode=_mode_label(lang, selected)))
    with contextlib.suppress(Exception):
        await callback.message.edit_text(
            text(lang, "settings_title", mode=_mode_label(lang, selected)),
            reply_markup=settings_keyboard(lang, selected),
        )


@router.callback_query(F.data.startswith("lang:"))
async def on_language_pick(callback: CallbackQuery, bot: Bot) -> None:
    code = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    srv = get_services()
    if code not in LANGUAGE_NAMES:
        await callback.answer()
        return
    # The user may not exist yet when they only ever used inline mode.
    await srv.db.upsert_user(user_id, getattr(callback.from_user, "username", None), int(time.time()))
    await srv.db.set_language(user_id, code)
    await callback.answer(get_text(code, "language_set", lang=LANGUAGE_NAMES[code]))
    with contextlib.suppress(Exception):  # callback message may be inline
        await callback.message.edit_text(get_text(code, "language_set", lang=LANGUAGE_NAMES[code]))
