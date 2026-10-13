"""SQLite persistence layer built on aiosqlite.

Schema is created and migrated on startup using a small versioned migration
list. All queries are async; the code in this module never blocks.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

# version -> list of SQL statements
MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY,
            username   TEXT,
            language   TEXT,
            first_seen INTEGER,
            last_seen  INTEGER,
            banned     INTEGER NOT NULL DEFAULT 0,
            active     INTEGER NOT NULL DEFAULT 1
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS downloads (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            url_hash   TEXT,
            site       TEXT,
            format_key TEXT,
            status     TEXT,
            error      TEXT,
            size_bytes INTEGER,
            duration_s REAL,
            created_at INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cache (
            url_hash   TEXT,
            format_key TEXT,
            file_id    TEXT,
            title      TEXT,
            size_bytes INTEGER,
            created_at INTEGER,
            PRIMARY KEY (url_hash, format_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS deeplinks (
            token      TEXT PRIMARY KEY,
            url        TEXT,
            created_at INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS counters (
            key   TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at INTEGER
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_downloads_user   ON downloads(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_downloads_created ON downloads(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_downloads_site    ON downloads(site)",
        "CREATE INDEX IF NOT EXISTS idx_cache_created     ON cache(created_at)",
    ],
}


def url_hash(url: str) -> str:
    """Stable hash of a normalized URL used as a cache/stats key."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def path(self) -> str:
        return self._path

    async def connect(self) -> None:
        from pathlib import Path

        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()
        await self._conn.commit()
        log.info("database ready at %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate(self) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at INTEGER)"
        )
        cur = await self._conn.execute("SELECT MAX(version) AS v FROM schema_version")
        row = await cur.fetchone()
        current = int(row["v"]) if row and row["v"] is not None else 0
        for version in sorted(MIGRATIONS):
            if version <= current:
                continue
            for stmt in MIGRATIONS[version]:
                await self._conn.execute(stmt)
            await self._conn.execute(
                "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, int(time.time())),
            )
            log.info("applied DB migration v%d", version)

    # -- transaction helper --------------------------------------------------

    async def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        assert self._conn is not None
        await self._conn.execute(sql, params)
        await self._conn.commit()

    # -- users ---------------------------------------------------------------

    async def upsert_user(self, user_id: int, username: str | None, first_seen: int) -> None:
        now = int(time.time())
        await self._execute(
            """
            INSERT INTO users (id, username, first_seen, last_seen, banned, active)
            VALUES (?, ?, ?, ?, 0, 1)
            ON CONFLICT(id) DO UPDATE SET
                username = COALESCE(?, username),
                last_seen = ?,
                active = 1
            """,
            (user_id, username, first_seen, now, username, now),
        )

    async def touch_user(self, user_id: int) -> None:
        await self._execute("UPDATE users SET last_seen = ?, active = 1 WHERE id = ?", (int(time.time()), user_id))

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return await cur.fetchone()

    async def get_language(self, user_id: int) -> str | None:
        row = await self.get_user(user_id)
        return row["language"] if row else None

    async def set_language(self, user_id: int, language: str) -> None:
        await self._execute("UPDATE users SET language = ? WHERE id = ?", (language, user_id))

    async def is_banned(self, user_id: int) -> bool:
        row = await self.get_user(user_id)
        if not row:
            return False
        return bool(row["banned"])

    async def set_banned(self, user_id: int, banned: bool) -> None:
        await self._execute("UPDATE users SET banned = ?, active = 1 WHERE id = ?", (1 if banned else 0, user_id))

    async def mark_inactive(self, user_id: int) -> None:
        await self._execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))

    async def all_user_ids(self, include_banned: bool = False) -> list[int]:
        assert self._conn is not None
        query = "SELECT id FROM users"
        if not include_banned:
            query += " WHERE banned = 0 AND active = 1"
        cur = await self._conn.execute(query)
        rows = await cur.fetchall()
        return [int(r["id"]) for r in rows]

    # -- downloads -----------------------------------------------------------

    async def record_download(
        self,
        user_id: int,
        hash_: str,
        site: str,
        format_key: str,
        status: str,
        error: str | None = None,
        size_bytes: int | None = None,
        duration_s: float | None = None,
    ) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(
            """
            INSERT INTO downloads (user_id, url_hash, site, format_key, status, error,
                                   size_bytes, duration_s, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, hash_, site, format_key, status, error, size_bytes, duration_s, int(time.time())),
        )
        await self._conn.commit()
        return int(cur.lastrowid or 0)

    async def downloads_total(self) -> int:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT COUNT(*) AS c FROM downloads")
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def downloads_today(self) -> int:
        start = int(time.time()) - 24 * 3600
        assert self._conn is not None
        cur = await self._conn.execute("SELECT COUNT(*) AS c FROM downloads WHERE created_at >= ?", (start,))
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def thumbnails_total(self) -> int:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT COUNT(*) AS c FROM downloads WHERE format_key = 'thumbnail'")
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def failures_by_type(self, limit: int = 10) -> list[tuple[str, int]]:
        assert self._conn is not None
        cur = await self._conn.execute(
            """
            SELECT COALESCE(error, 'ok') AS err_type, COUNT(*) AS c
            FROM downloads WHERE status = 'error'
            GROUP BY err_type ORDER BY c DESC LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
        return [(str(r["err_type"]), int(r["c"])) for r in rows]

    async def top_sites(self, limit: int = 5) -> list[tuple[str, int]]:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT COALESCE(site, 'unknown') AS s, COUNT(*) AS c FROM downloads GROUP BY s ORDER BY c DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [(str(r["s"]), int(r["c"])) for r in rows]

    # -- counters (cache hit / miss) ----------------------------------------

    async def _counter_get(self, key: str) -> int:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT value FROM counters WHERE key = ?", (key,))
        row = await cur.fetchone()
        return int(row["value"]) if row else 0

    async def counter_incr(self, key: str, by: int = 1) -> None:
        await self._execute(
            "INSERT INTO counters (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = value + ?",
            (key, by, by),
        )

    async def cache_hit_rate(self) -> tuple[float, int, int]:
        hits = await self._counter_get("cache:hit")
        misses = await self._counter_get("cache:miss")
        total = hits + misses
        rate = (hits / total) if total else 0.0
        return rate, hits, misses

    # -- cache ---------------------------------------------------------------

    async def cache_get(self, hash_: str, format_key: str, ttl_s: int) -> aiosqlite.Row | None:
        assert self._conn is not None
        now = int(time.time())
        cur = await self._conn.execute(
            "SELECT * FROM cache WHERE url_hash = ? AND format_key = ? AND created_at > ?",
            (hash_, format_key, now - ttl_s),
        )
        return await cur.fetchone()

    async def cache_put(self, hash_: str, format_key: str, file_id: str, title: str, size_bytes: int) -> None:
        await self._execute(
            """
            INSERT INTO cache (url_hash, format_key, file_id, title, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url_hash, format_key) DO UPDATE SET
                file_id = excluded.file_id, title = excluded.title,
                size_bytes = excluded.size_bytes, created_at = excluded.created_at
            """,
            (hash_, format_key, file_id, title, size_bytes, int(time.time())),
        )

    async def purge_cache_expired(self, ttl_s: int) -> int:
        assert self._conn is not None
        now = int(time.time())
        cur = await self._conn.execute("DELETE FROM cache WHERE created_at < ?", (now - ttl_s))
        await self._conn.commit()
        return int(cur.rowcount)

    # -- deeplinks -----------------------------------------------------------

    async def deeplink_create(self, token: str, url: str) -> None:
        await self._execute(
            "INSERT OR REPLACE INTO deeplinks (token, url, created_at) VALUES (?, ?, ?)",
            (token, url, int(time.time())),
        )

    async def deeplink_get(self, token: str) -> aiosqlite.Row | None:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT * FROM deeplinks WHERE token = ?", (token,))
        return await cur.fetchone()

    async def deeplink_delete(self, token: str) -> None:
        await self._execute("DELETE FROM deeplinks WHERE token = ?", (token,))

    async def purge_deeplinks(self, max_age_s: int) -> int:
        assert self._conn is not None
        now = int(time.time())
        cur = await self._conn.execute("DELETE FROM deeplinks WHERE created_at < ?", (now - max_age_s))
        await self._conn.commit()
        return int(cur.rowcount)

    # -- stats ---------------------------------------------------------------

    async def total_users(self) -> int:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT COUNT(*) AS c FROM users")
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def active_users_since(self, seconds: int) -> int:
        assert self._conn is not None
        cutoff = int(time.time()) - seconds
        cur = await self._conn.execute("SELECT COUNT(*) AS c FROM users WHERE last_seen >= ? AND active = 1", (cutoff,))
        row = await cur.fetchone()
        return int(row["c"]) if row else 0
