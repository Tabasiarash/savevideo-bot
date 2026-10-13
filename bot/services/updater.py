"""yt-dlp self-update helpers.

The CLI is installed into the same Python environment the bot runs in, so a
``pip install -U yt-dlp`` inside this venv updates what the subprocess runner
executes. Updates run off the event loop as subprocesses.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_RETRY_HINTS = (
    "unable to download webpage",
    "get_webpage_headers",
    "this api does not exist",
    "no such extractor",
    "sign in to confirm",
    "confirm you're not a bot",
    "http error 4",
    "unsupported url",
    "is not a valid url",
    "extractor",
    "requested format is not available",
    "no video formats found",
)


async def run_cmd(args: list[str], timeout: int = 180) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"timed out after {timeout}s"
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


async def cli_version() -> str:
    """Version string as the yt-dlp CLI (fresh subprocess import)."""
    code, out = await run_cmd([sys.executable, "-m", "yt_dlp", "--version"], timeout=60)
    return out.strip() or f"unknown (exit {code})"


@dataclass
class UpdateResult:
    ok: bool
    old_version: str
    new_version: str
    message: str


class Updater:
    @staticmethod
    def should_retry(error_text: str) -> bool:
        """Heuristic: does this failure smell like a stale extractor?"""
        lowered = error_text.lower()
        return any(hint in lowered for hint in _RETRY_HINTS)

    async def update(self) -> UpdateResult:
        old = await cli_version()
        pip = [sys.executable, "-m", "pip", "install", "--no-input", "-U", "yt-dlp"]
        code, out = await run_cmd(pip)
        if code != 0 and "--break-system-packages" not in sys.argv:
            code, out2 = await run_cmd(pip + ["--break-system-packages"])
            out = f"{out}\n{out2}" if out else out2
        new = await cli_version()
        if code != 0:
            return UpdateResult(ok=False, old_version=old, new_version=new, message=out[-400:] or "pip failed")
        return UpdateResult(ok=True, old_version=old, new_version=new, message=out[-400:])


async def auto_update_loop(services) -> None:  # pragma: no cover - integration glue
    """Background task: refresh yt-dlp every ``UPDATE_CHECK_HOURS`` hours."""
    from bot.config import settings
    from bot.i18n import get_text

    hours = max(1, settings.update_check_hours)
    while True:
        await asyncio.sleep(hours * 3600)
        try:
            result = await services.updater.update()
            if result.ok:
                log.info("auto yt-dlp update: %s -> %s", result.old_version, result.new_version)
            else:
                log.warning("auto yt-dlp update failed: %s", result.message)
                for admin in settings.admin_ids:
                    with contextlib.suppress(Exception):
                        await services.bot.send_message(
                            admin,
                            get_text("en", "ytdlp_update_failed", error=result.message),
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("auto update loop error: %s", exc)


def cookie_file_exists(path: str) -> bool:
    return bool(path) and Path(path).exists()
