"""Entry point: wires up services, the Bot, routers and long-polling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.deps import init_services
from bot.handlers import admin, inline, links, spotify, start
from bot.handlers.common import active_temp_dirs
from bot.services.downloader import sweep_temp_dir
from bot.services.spotify.metadata import META_TTL_S
from bot.services.updater import auto_update_loop

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("savevideo-bot")


def build_bot() -> Bot:
    defaults = DefaultBotProperties(parse_mode=ParseMode.HTML)
    if settings.bot_api_url:
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.client.telegram import TelegramAPIServer

        api = TelegramAPIServer.from_base(settings.bot_api_url, is_local=True)
        session = AiohttpSession(api=api, timeout=settings.upload_timeout_seconds)
        log.info(
            "using local Bot API at %s (upload limit %s MB)",
            settings.bot_api_url,
            settings.max_upload_mb,
        )
        return Bot(token=settings.bot_token, session=session, default=defaults)
    return Bot(token=settings.bot_token, default=defaults)


async def verify_bot_api(bot: Bot) -> None:
    """Fail fast if the configured Bot API server is not reachable."""
    target = settings.bot_api_url or "https://api.telegram.org"
    mode = "local" if settings.is_local_api else "cloud"
    try:
        me = await bot.get_me()
    except Exception as exc:  # noqa: BLE001
        log.error("Bot API unreachable at %s (mode=%s): %s", target, mode, exc)
        raise
    log.info(
        "Bot API reachable at %s (mode=%s, max_upload=%s MB) as @%s",
        target,
        mode,
        settings.max_upload_mb,
        me.username,
    )


def build_dispatcher() -> Dispatcher:
    """Wire routers in the exact order used in production.

    Command routers (``start``, ``admin``) MUST come before ``links``: the
    latter has a catch-all ``F.text`` handler that would otherwise swallow every
    command and reply "Unknown command".

    ``spotify`` also comes before ``links`` (and is registered even when the
    feature is off) so Spotify URLs never reach the generic link handler; its
    filter only matches messages that contain a Spotify URL.
    """
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(spotify.router)
    dp.include_router(links.router)
    dp.include_router(inline.router)
    return dp


PUBLIC_COMMANDS = [
    types.BotCommand(command="start", description="Start / show help"),
    types.BotCommand(command="help", description="How to use the bot"),
    types.BotCommand(command="language", description="Change language"),
]

ADMIN_COMMANDS = [
    types.BotCommand(command="stats", description="Usage statistics"),
    types.BotCommand(command="users", description="Recent users"),
    types.BotCommand(command="broadcast", description="Message every user"),
    types.BotCommand(command="ban", description="Ban a user"),
    types.BotCommand(command="unban", description="Unban a user"),
    types.BotCommand(command="update_ytdlp", description="Update yt-dlp now"),
    types.BotCommand(command="cleanup", description="Free temp disk space"),
    types.BotCommand(command="health", description="Health / environment report"),
]


async def _set_commands(bot: Bot) -> None:
    """Populate the blue command menu.

    Everyone sees the public commands; each admin also gets the admin commands
    in their own private chat (a chat scope overrides the default list there).
    """
    with contextlib.suppress(Exception):
        await bot.set_my_commands(PUBLIC_COMMANDS)
    admin_menu = PUBLIC_COMMANDS + ADMIN_COMMANDS
    for admin_id in settings.admin_ids:
        with contextlib.suppress(Exception):
            await bot.set_my_commands(
                admin_menu,
                scope=types.BotCommandScopeChat(chat_id=admin_id),
            )


def sweep_stale_tmp(temp_dir: Path, max_age_s: float = 3600) -> int:
    """Delete stale temp dirs; never touch live flows or the download-link dir."""
    result = sweep_temp_dir(
        temp_dir,
        max_age_s=max_age_s,
        protected=active_temp_dirs(),
        keep_within=[settings.download_link_dir_resolved],
    )
    if result.removed:
        log.info("swept %d stale temp entries (%d bytes)", result.removed, result.freed_bytes)
    return result.removed


def _link_dir_warning() -> str | None:
    """Warn when DOWNLOAD_LINK_DIR is not on the same filesystem as TEMP_DIR."""
    if not settings.download_link_enabled:
        return None
    try:
        temp_dev = settings.temp_dir_resolved.stat().st_dev
        link_dev = settings.download_link_dir_resolved.stat().st_dev
    except OSError:
        return None
    if temp_dev != link_dev:
        return (
            "DOWNLOAD_LINK_DIR is on a different filesystem than TEMP_DIR; "
            "moving finished files will be a slow copy, not a rename"
        )
    return None


async def _maintenance_loop(srv) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await srv.db.purge_deeplinks(24 * 3600)
            await srv.db.purge_cache_expired(settings.cache_ttl_s)
            await srv.db.spotify_meta_purge_expired(META_TTL_S)
            await srv.download_links.cleanup(srv.db)
            sweep_stale_tmp(settings.temp_dir_resolved)
        except Exception:  # noqa: BLE001
            log.warning("maintenance sweep failed", exc_info=True)


def _log_environment() -> None:
    import yt_dlp

    try:
        from yt_dlp.version import __version__ as ytdlp_version
    except Exception:  # noqa: BLE001
        ytdlp_version = getattr(getattr(yt_dlp, "version", None), "VERSION", "unknown")
    log.info("yt-dlp %s", ytdlp_version)
    ffmpeg = shutil.which("ffmpeg")
    log.info("ffmpeg %s", "available" if ffmpeg else "MISSING (merge/thumbnail disabled)")
    log.info("queue max_jobs=%s upload_limit=%sMB temp=%s db=%s", settings.max_jobs, settings.max_mb, settings.temp_dir_resolved, settings.db_path)
    if settings.download_link_enabled:
        log.info(
            "download links enabled (dir=%s ttl=%sh max_active=%s max_total=%sGB)",
            settings.download_link_dir_resolved,
            settings.download_link_ttl_hours,
            settings.download_link_max_active,
            settings.download_link_max_total_gb,
        )
    warning = _link_dir_warning()
    if warning:
        log.warning("%s", warning)


async def main() -> None:
    if not settings.bot_token:
        log.error("BOT_TOKEN is not set; add it to .env or the environment.")
        sys.exit(2)

    srv = init_services(settings)
    settings.temp_dir_resolved.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.download_link_enabled:
        settings.download_link_dir_resolved.mkdir(parents=True, exist_ok=True)

    bot = build_bot()
    srv.bot = bot
    await verify_bot_api(bot)

    await srv.db.connect()
    await srv.db.purge_cache_expired(settings.cache_ttl_s)
    await srv.db.spotify_meta_purge_expired(META_TTL_S)
    sweep_stale_tmp(settings.temp_dir_resolved)
    with contextlib.suppress(Exception):
        await srv.download_links.cleanup(srv.db)  # reconcile link files/rows at startup
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
