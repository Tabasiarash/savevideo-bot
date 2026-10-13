"""Handlers package: user-facing entry points for aiogram."""

from bot.handlers import admin, inline, links, start  # noqa: F401

__all__ = ["admin", "inline", "links", "start"]
