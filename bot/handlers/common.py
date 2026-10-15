"""Shared handler helpers."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from aiogram.types import Message

from bot.config import settings
from bot.db import Database
from bot.i18n import get_text, language_code_to_code

log = logging.getLogger(__name__)


async def register_user(db: Database, message: Message) -> str:
    """Upsert the user and return their resolved language code."""
    assert message.from_user is not None
    user = message.from_user
    await db.upsert_user(user.id, user.username, int(time.time()))
    lang = await db.get_language(user.id)
    if not lang:
        lang = language_code_to_code(user.language_code, settings.default_language)
    return lang


async def resolve_language(db: Database, user_id: int, fallback_language_code: str | None) -> str:
    stored = await db.get_language(user_id)
    if stored:
        return stored
    return language_code_to_code(fallback_language_code, settings.default_language)


async def is_banned(db: Database, user_id: int) -> bool:
    return await db.is_banned(user_id)


async def safe_delete(message: Message) -> None:
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        log.debug("could not delete message %s", getattr(message, "message_id", None))


def text(lang_code: str, key: str, **kwargs) -> str:
    return get_text(lang_code, key, settings.default_language, **kwargs)


def active_temp_dirs() -> set[Path]:
    """Temp directories belonging to live flows, which must never be swept.

    Imported lazily to avoid a circular import at module load time.
    """
    from bot.handlers import links, spotify

    dirs: set[Path] = set()
    for flow in getattr(links, "_pending", {}).values():
        tmpdir = getattr(flow, "tmpdir", None)
        if tmpdir is not None:
            dirs.add(Path(tmpdir))
    for state in getattr(spotify, "_matches", {}).values():
        tmpdir = getattr(state, "tmpdir", None)
        if tmpdir is not None:
            dirs.add(Path(tmpdir))
    return dirs
