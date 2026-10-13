"""Admin commands: /stats, /broadcast, /ban, /unban, /update_ytdlp, /health.

Only users whose numeric IDs are in ``ADMIN_IDS`` may use these.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.deps import get_services
from bot.handlers.common import text
from bot.services.downloader import (
    curl_cffi_version,
    detect_js_runtime,
    disk_free_bytes,
    mask_proxy,
)
from bot.services.updater import cli_version
from bot.utils import format_size

log = logging.getLogger(__name__)

router = Router()

_broadcast_lock = asyncio.Lock()


def _is_admin(message: Message) -> bool:
    return message.from_user is not None and settings.is_admin(message.from_user.id)


async def _denied(message: Message) -> None:
    await message.answer(text("en", "not_allowed"))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message):
        await _denied(message)
        return
    srv = get_services()
    total = await srv.db.total_users()
    active24 = await srv.db.active_users_since(24 * 3600)
    active7 = await srv.db.active_users_since(7 * 24 * 3600)
    today = await srv.db.downloads_today()
    total_dl = await srv.db.downloads_total()
    thumbs = await srv.db.thumbnails_total()
    rate, hits, misses = await srv.db.cache_hit_rate()
    sites = ",".join(f"{s}×{c}" for s, c in await srv.db.top_sites(5)) or text("en", "stats_empty")
    failures = ",".join(f"{e}:{c}" for e, c in await srv.db.failures_by_type(10)) or text("en", "stats_empty")
    disk = disk_free_bytes(settings.temp_dir_resolved)
    log.info("stats cmd admin_id=%s users=%d", message.from_user.id, total)
    await message.answer(
        text(
            "en",
            "stats",
            total=total,
            active24=active24,
            active7=active7,
            today=today,
            total_downloads=total_dl,
            thumbnails=thumbs,
            cache_hit=f"{rate * 100:.1f}",
            sites=sites,
            failures=failures,
            tmp_size=format_size(disk),
        )
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await _denied(message)
        return
    body = (command.args or "").strip()
    if not body:
        await message.answer(text("en", "broadcast_no_text"))
        return
    if _broadcast_lock.locked():
        await message.answer(text("en", "broadcast_running"))
        return
    srv = get_services()
    bot: Bot = srv.bot
    user_ids = await srv.db.all_user_ids(include_banned=False)

    async with _broadcast_lock:
        ok = fail = blocked = 0
        acknowledging = await message.answer(text("en", "broadcast_running"))
        for index, user_id in enumerate(user_ids, start=1):
            try:
                await bot.send_message(user_id, body)
                ok += 1
            except Exception as exc:  # noqa: BLE001
                error = str(exc).lower()
                if "forbidden" in error or "bot was blocked" in error or "user is deactivated" in error:
                    await srv.db.mark_inactive(user_id)
                    blocked += 1
                else:
                    fail += 1
            if index % int(max(1, settings.broadcast_rate)) == 0:
                await asyncio.sleep(0.05)
        try:
            await acknowledging.edit_text(text("en", "broadcast_done", ok=ok, fail=fail, blocked=blocked))
        except Exception:  # noqa: BLE001
            await message.answer(text("en", "broadcast_done", ok=ok, fail=fail, blocked=blocked))


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await _denied(message)
        return
    target = _parse_user_id(command.args)
    if target is None:
        await message.answer(text("en", "broadcast_no_text"))
        return
    await get_services().db.set_banned(target, True)
    await message.answer(text("en", "ban_done", user_id=target))


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await _denied(message)
        return
    target = _parse_user_id(command.args)
    if target is None:
        await message.answer(text("en", "broadcast_no_text"))
        return
    await get_services().db.set_banned(target, False)
    await message.answer(text("en", "unban_done", user_id=target))


@router.message(Command("update_ytdlp"))
async def cmd_update_ytdlp(message: Message) -> None:
    if not _is_admin(message):
        await _denied(message)
        return
    srv = get_services()
    result = await srv.updater.update()
    if result.ok:
        body = text("en", "ytdlp_updated", old=result.old_version, new=result.new_version)
    else:
        body = text("en", "ytdlp_update_failed", error=result.message)
    await message.answer(body)


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    if not _is_admin(message):
        await _denied(message)
        return
    srv = get_services()
    version = await cli_version()
    ffmpeg = _ffmpeg_version()
    free = disk_free_bytes(settings.temp_dir_resolved)
    mode = "local" if settings.is_local_api else "cloud"
    probe = await srv.runner.probe_egress(settings.egress_test_url)
    egress = f"✅ {probe.detail}" if probe.ok else f"❌ {probe.detail}"
    await message.answer(
        text(
            "en",
            "health",
            ytdlp=version,
            ffmpeg=ffmpeg or "not found",
            disk=format_size(free),
            queue_len=srv.queue.waiting,
            active=srv.queue.active,
            mode=mode,
            limit=settings.max_mb,
            api=await _bot_api_status(srv),
            data_dir=_data_dir_status(),
            proxy=mask_proxy(settings.proxy_url),
            cookies=_cookies_status(settings.cookies_file),
            js_runtime=detect_js_runtime(settings) or "none",
            curl_cffi=curl_cffi_version() or "not installed",
            ip_family=_ip_family_label(),
            extra_args=settings.ytdlp_extra_args or "none",
            egress=egress,
        )
    )


async def _bot_api_status(srv) -> str:
    if not settings.is_local_api:
        return "cloud"
    try:
        me = await srv.bot.me()
    except Exception as exc:  # noqa: BLE001
        return f"unreachable ({type(exc).__name__})"
    username = getattr(me, "username", None)
    return f"ok @{username}" if username else "ok"


def _data_dir_status() -> str:
    if not settings.is_local_api:
        return "n/a"
    try:
        return format_size(disk_free_bytes(settings.bot_api_data_dir))
    except OSError:
        return "unavailable"


def _cookies_status(path: str) -> str:
    if not path:
        return "none"
    p = Path(path)
    if not p.exists():
        return "missing"
    days = (time.time() - p.stat().st_mtime) / 86400
    return f"set ({days:.0f}d old)"


def _ip_family_label() -> str:
    if settings.force_ipv4:
        return "IPv4 only"
    if settings.force_ipv6:
        return "IPv6 for YouTube"
    return "auto"


def _parse_user_id(arg: str | None) -> int | None:
    if not arg:
        return None
    try:
        return int(arg.strip().lstrip("@"))
    except ValueError:
        return None


def _ffmpeg_version() -> str:
    import shutil
    import subprocess

    binary = shutil.which("ffmpeg")
    if not binary:
        return ""
    try:
        out = subprocess.run(
            [binary, "-version"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.splitlines()[0] if out.stdout else ""
    except (subprocess.SubprocessError, OSError):
        return ""
