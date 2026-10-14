"""Over-limit delivery: keep a finished file on disk and hand the user a signed,
expiring URL served by nginx.

Splitting and re-encoding are gone: anything the Bot API cannot accept becomes a
link instead. The token formula below MUST match the nginx directive exactly::

    secure_link $arg_md5,$arg_expires;
    secure_link_md5 "$secure_link_expires$uri $download_link_secret";

i.e. ``base64url(md5(f"{expires}{uri} {secret}"))`` with ``=`` padding stripped.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from bot.config import Settings

log = logging.getLogger(__name__)

# On-disk link filenames are restricted to a URL-path-safe, ASCII-only set so the
# signed URI is byte-identical to nginx's normalised ``$uri``.
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class LinkError(Exception):
    """Link delivery failed for a reason the caller should surface to the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # disabled | quota_user | quota_total | error


@dataclass(frozen=True)
class LinkResult:
    url: str
    expires_at: int
    size_bytes: int


def secure_link_token(uri: str, expires: int, secret: str) -> str:
    """Reproduce nginx ``secure_link``'s MD5 token (order: expires, uri, secret)."""
    raw = hashlib.md5(f"{expires}{uri} {secret}".encode()).digest()  # noqa: S324 — nginx secure_link uses MD5
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def safe_filename(name: str, max_len: int = 120) -> str:
    """Reduce ``name`` to an ASCII, URL-path-safe base filename."""
    cleaned = _UNSAFE_RE.sub("_", (name or "").strip())
    cleaned = cleaned.strip("._-") or "download"
    return cleaned[:max_len].strip("._-") or "download"


class DownloadLinkStore:
    """Stores over-limit files and mints signed, expiring URLs for them."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.download_link_enabled

    @property
    def root(self) -> Path:
        return self.settings.download_link_dir_resolved

    async def create(self, db, *, source: Path, user_id: int, title: str) -> LinkResult:
        if not self.enabled:
            raise LinkError("disabled")

        now = int(time.time())
        expires = now + self.settings.download_link_ttl_s
        size = source.stat().st_size

        if await db.download_link_count_active(user_id, now) >= self.settings.download_link_max_active:
            raise LinkError("quota_user")
        max_total = int(self.settings.download_link_max_total_gb * 1024**3)
        if max_total > 0 and await db.download_link_total_bytes() + size > max_total:
            raise LinkError("quota_total")

        link_id = secrets.token_hex(16)
        filename = safe_filename(source.name or title)
        dest_dir = self.root / link_id
        dest = dest_dir / filename
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            self._move(source, dest)
        except OSError as exc:
            shutil.rmtree(dest_dir, ignore_errors=True)
            log.warning("download-link move failed: %s", exc)
            raise LinkError("error") from exc

        uri = f"/dl/{link_id}/{filename}"
        token = secure_link_token(uri, expires, self.settings.download_link_secret)
        await db.download_link_create(
            token=token,
            user_id=user_id,
            filename=filename,
            rel_path=f"{link_id}/{filename}",
            size_bytes=size,
            created_at=now,
            expires_at=expires,
        )
        base = self.settings.download_link_base_url.rstrip("/")
        return LinkResult(url=f"{base}{uri}?md5={token}&expires={expires}", expires_at=expires, size_bytes=size)

    @staticmethod
    def _move(source: Path, dest: Path) -> None:
        try:
            os.rename(source, dest)  # instant when on the same filesystem
        except OSError:
            # Cross-device (misconfiguration): fall back to a copy so delivery
            # still works, but this is a slow path we warn about.
            log.warning("download-link across filesystems; copying instead of renaming")
            shutil.copyfile(source, dest)
            with contextlib.suppress(OSError):
                os.unlink(source)

    async def cleanup(self, db) -> None:
        """Drop expired links, orphan files and rows whose file is gone."""
        now = int(time.time())
        rows = await db.download_link_all()
        known: set[str] = set()
        for row in rows:
            rel = str(row["rel_path"])
            known.add(rel)
            path = self.root / rel
            if int(row["expires_at"]) <= now or not path.exists():
                self._remove(path)
                await db.download_link_delete(str(row["token"]))

        if not self.root.exists():
            return
        for child in self.root.iterdir():
            if not child.is_dir():
                self._remove(child)
                continue
            for entry in child.iterdir():
                if f"{child.name}/{entry.name}" not in known:
                    self._remove(entry)
            with contextlib.suppress(OSError):
                child.rmdir()

    @staticmethod
    def _remove(path: Path) -> None:
        with contextlib.suppress(OSError):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
