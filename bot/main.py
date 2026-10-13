"""Entry point: wires up services, the Bot, routers and long-polling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.deps import init_services
from bot.handlers import admin, inline, links, start
from bot.services.updater import auto_update_loop

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("savevideo-bot")


def build_bot() -> Bot:
    defaults = DefaultBotProperties(parse_mode=ParseMode.HTML)
    if settings.local_api or (settings.telegram_api_id and settings.telegram_api_hash):
        from aiogram.client.api import TelegramAPIServer
        from aiogram.client.session.aiohttp import AiohttpSession

        api = TelegramAPIServer.from_base(settings.local_api_base)
        session = AiohttpSession(api=api)
        log.info("using local Bot API at %s", settings.local_api_base)
        return Bot(token=settings.bot_token, session=session, default=defaults)
    return Bot(token=settings.bot_token, default=defaults)


def build_dispatcher() -> Dispatcher:
    """Wire routers in the exact order used in production.

    Command routers (``start``, ``admin``) MUST come before ``links``: the
    latter has a catch-all ``F.text`` handler that would otherwise swallow every
    command and reply "Unknown command".
    """
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(links.router)
    dp.include_router(inline.router)
    return dp


async def _set_commands(bot: Bot) -> None:
    with contextlib.suppress(Exception):
        await bot.set_my_commands(
            [
                types.BotCommand(command="start", description="Start / show help"),
                types.BotCommand(command="help", description="How to use the bot"),
                types.BotCommand(command="language", description="Change language"),
            ]
        )


def sweep_stale_tmp(temp_dir: Path, max_age_s: int = 3600) -> None:
    if not temp_dir.exists():
        return
    now = time.time()
    for entry in temp_dir.iterdir():
        if entry.is_dir():
            try:
                if now - entry.stat().st_mtime > max_age_s:
                    import shutil

                    shutil.rmtree(entry, ignore_errors=True)
                    log.info("swept stale temp dir %s", entry.name)
            except OSError:
                continue


async def _maintenance_loop(srv) -> None:
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            await srv.db.purge_deeplinks(24 * 3600)
            await srv.db.purge_cache_expired(settings.cache_ttl_s)
        except Exception:  # noqa: BLE001
            log.warning("maintenance sweep failed", exc_info=True)


def _log_environment() -> None:
    import shutil

    import yt_dlp

    try:
        from yt_dlp.version import __version__ as ytdlp_version
    except Exception:  # noqa: BLE001
        ytdlp_version = getattr(getattr(yt_dlp, "version", None), "VERSION", "unknown")
    log.info("yt-dlp %s", ytdlp_version)
    ffmpeg = shutil.which("ffmpeg")
    log.info("ffmpeg %s", "available" if ffmpeg else "MISSING (re-encode/split disabled)")
    log.info("queue max_jobs=%s upload_limit=%sMB temp=%s db=%s", settings.max_jobs, settings.max_mb, settings.temp_dir_resolved, settings.db_path)


async def main() -> None:
    if not settings.bot_token:
        log.error("BOT_TOKEN is not set; add it to .env or the environment.")
        sys.exit(2)

    srv = init_services(settings)
    settings.temp_dir_resolved.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    bot = build_bot()
    srv.bot = bot

    await srv.db.connect()
    await srv.db.purge_cache_expired(settings.cache_ttl_s)
    sweep_stale_tmp(settings.temp_dir_resolved)
    _log_environment()

    dp = build_dispatcher()
    await _set_commands(bot)

    background_tasks = []
    if settings.auto_update_ytdlp:
        background_tasks.append(asyncio.create_task(auto_update_loop(srv)))
    background_tasks.append(asyncio.create_task(_maintenance_loop(srv)))

    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"],
        )
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        await bot.session.close()
        await srv.db.close()
        log.info("shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("interrupted")
