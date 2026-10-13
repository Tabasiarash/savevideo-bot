"""Concurrency limiter: a FIFO queue with position reporting.

Only ``MAX_JOBS`` downloads run at once; everyone else blocks on an event-loop
condition and is told their queue position.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class QueueAdmission:
    position: int
    waited: bool


class JobQueue:
    def __init__(self, max_jobs: int) -> None:
        self._max = max(1, max_jobs)
        self._active = 0
        self._waiting = 0
        self._cond = asyncio.Condition()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return self._waiting

    async def enter(self) -> QueueAdmission:
        """Wait for a slot; returns position (1 == next to run)."""
        async with self._cond:
            if self._active < self._max:
                self._active += 1
                return QueueAdmission(position=1, waited=False)
            self._waiting += 1
            position = self._waiting
        try:
            async with self._cond:
                await self._cond.wait_for(lambda: self._active < self._max)
                self._waiting = max(0, self._waiting - 1)
                self._active += 1
                return QueueAdmission(position=position, waited=True)
        except BaseException:
            async with self._cond:
                self._waiting = max(0, self._waiting - 1)
            raise

    async def leave(self) -> None:
        async with self._cond:
            self._active = max(0, self._active - 1)
            if self._active < self._max:
                self._cond.notify_all()
