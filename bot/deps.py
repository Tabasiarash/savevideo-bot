"""Shared service container.

Services are created once at startup and registered here; handlers access them
through :func:`get_services`. Tests replace individual services with fakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bot.config import Settings
from bot.db import Database

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database
    runner: object = field(default=None)  # set by init_services
    queue: object = field(default=None)
    ratelimiter: object = field(default=None)
    cache: object = field(default=None)
    updater: object = field(default=None)
    bot: object = field(default=None)  # assigned by main.py after bot creation


_services: Services | None = None


def init_services(settings: Settings) -> Services:
    global _services
    if _services is not None:
        return _services
    from bot.services.cache import DownloadCache
    from bot.services.downloader import YtDlpRunner
    from bot.services.queue import JobQueue
    from bot.services.ratelimit import RateLimiter
    from bot.services.updater import Updater

    db = Database(settings.db_path)
    _services = Services(
        settings=settings,
        db=db,
        runner=YtDlpRunner(settings),
        queue=JobQueue(settings.max_jobs),
        ratelimiter=RateLimiter(db, settings),
        cache=DownloadCache(db, settings),
        updater=Updater(),
    )
    return _services


def get_services() -> Services:
    if _services is None:
        raise RuntimeError("services not initialised: call init_services() first")
    return _services


def reset_services() -> None:
    """Drop the service container (used by tests)."""
    global _services
    _services = None
