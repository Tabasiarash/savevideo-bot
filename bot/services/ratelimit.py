"""Per-user rate limiting: cooldown, daily quota, active-job count.

Admins are exempt from all limits.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum

from bot.config import Settings
from bot.db import Database


class LimitReason(StrEnum):
    COOLDOWN = "cooldown"
    DAILY = "daily"
    ACTIVE_JOB = "active_job"


@dataclass
class RateStatus:
    allowed: bool
    reason: LimitReason | None = None
    seconds: int = 0


class RateLimiter:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._lock = asyncio.Lock()
        self._last_request: dict[int, float] = {}
        self._active: dict[int, int] = {}

    def _is_admin(self, user_id: int) -> bool:
        return self._settings.is_admin(user_id)

    async def _daily_count(self, user_id: int) -> int:
        from datetime import datetime

        start = int(datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        assert self._db._conn is not None
        cur = await self._db._conn.execute(
            "SELECT COUNT(*) AS c FROM downloads WHERE user_id = ? AND status = 'ok' AND created_at >= ?",
            (user_id, start),
        )
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    @property
    def _loop_time(self) -> float:
        return asyncio.get_running_loop().time()

    async def check(self, user_id: int) -> RateStatus:
        """Validate a new download *request* (not a callback follow-up)."""
        if self._is_admin(user_id):
            return RateStatus(allowed=True)
        now = self._loop_time
        last = self._last_request.get(user_id, 0.0)
        remaining = self._settings.cooldown_seconds - (now - last)
        if remaining > 0:
            return RateStatus(allowed=False, reason=LimitReason.COOLDOWN, seconds=int(remaining) + 1)
        count = await self._daily_count(user_id)
        if count >= self._settings.per_user_daily_limit:
            return RateStatus(allowed=False, reason=LimitReason.DAILY)
        return RateStatus(allowed=True)

    async def begin_request(self, user_id: int) -> None:
        async with self._lock:
            self._last_request[user_id] = self._loop_time

    async def start_job(self, user_id: int) -> bool:
        """Mark a download job active for the user; False if they already have one."""
        if self._is_admin(user_id):
            return True
        async with self._lock:
            if self._active.get(user_id, 0) >= 1:
                return False
            self._active[user_id] = self._active.get(user_id, 0) + 1
            return True

    async def has_active_job(self, user_id: int) -> bool:
        if self._is_admin(user_id):
            return False
        async with self._lock:
            return self._active.get(user_id, 0) >= 1

    async def finish_job(self, user_id: int) -> None:
        async with self._lock:
            current = self._active.get(user_id, 0)
            if current <= 1:
                self._active.pop(user_id, None)
            else:
                self._active[user_id] = current - 1
