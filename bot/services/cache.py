"""URL+format → Telegram ``file_id`` cache on top of SQLite."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config import Settings
from bot.db import Database


@dataclass(frozen=True)
class CacheEntry:
    url_hash: str
    format_key: str
    file_id: str
    title: str
    size_bytes: int
    created_at: int


class DownloadCache:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    async def get(self, url_hash: str, format_key: str) -> CacheEntry | None:
        row = await self._db.cache_get(url_hash, format_key, self._settings.cache_ttl_s)
        key = "cache:hit" if row else "cache:miss"
        await self._db.counter_incr(key)
        if not row:
            return None
        return CacheEntry(
            url_hash=str(row["url_hash"]),
            format_key=str(row["format_key"]),
            file_id=str(row["file_id"]),
            title=str(row["title"] or ""),
            size_bytes=int(row["size_bytes"] or 0),
            created_at=int(row["created_at"] or 0),
        )

    async def put(self, url_hash: str, format_key: str, file_id: str, title: str, size_bytes: int) -> None:
        await self._db.cache_put(url_hash, format_key, file_id, title, size_bytes)

    async def refresh(self, url_hash: str, format_key: str) -> None:
        """Keep a hit entry alive (re-put with same data)."""
        row = await self._db.cache_get(url_hash, format_key, self._settings.cache_ttl_s)
        if row:
            await self._db.cache_put(
                str(row["url_hash"]),
                str(row["format_key"]),
                str(row["file_id"]),
                str(row["title"] or ""),
                int(row["size_bytes"] or 0),
            )
