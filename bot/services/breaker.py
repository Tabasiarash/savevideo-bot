"""A tiny, dependency-free circuit breaker keyed by an arbitrary string.

The Spotify matcher uses one breaker per search source to stop hammering a
provider that keeps failing. The module is deliberately generic (any string key,
injectable clock) so other platform integrations can adopt it later; nothing
else is wired to it in this change.

Semantics: after ``threshold`` consecutive failures a key is *open* for
``cooldown`` seconds. While open, callers should skip it. Once the cooldown has
elapsed the key is treated as closed again (a single trial is then allowed), so a
recovered source is picked up automatically on the next attempt.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Entry:
    failures: int = 0
    opened_at: float | None = None


class Breaker:
    """Per-key failure counter with a cooldown."""

    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = max(1, int(threshold))
        self._cooldown = max(0.0, float(cooldown))
        self._clock = clock
        self._entries: dict[str, _Entry] = {}

    def is_open(self, key: str) -> bool:
        """True while ``key`` is in cooldown and should be skipped."""
        entry = self._entries.get(key)
        if entry is None or entry.opened_at is None:
            return False
        if self._clock() - entry.opened_at >= self._cooldown:
            # Cooldown elapsed: reset so the next attempt is a fresh trial.
            self._entries.pop(key, None)
            return False
        return True

    def record_failure(self, key: str) -> None:
        entry = self._entries.setdefault(key, _Entry())
        entry.failures += 1
        if entry.failures >= self._threshold and entry.opened_at is None:
            entry.opened_at = self._clock()

    def record_success(self, key: str) -> None:
        self._entries.pop(key, None)

    def failures(self, key: str) -> int:
        entry = self._entries.get(key)
        return entry.failures if entry else 0

    def reset(self) -> None:
        self._entries.clear()
