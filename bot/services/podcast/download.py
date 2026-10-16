"""Fetch a podcast enclosure with a hardened, redirect-aware HTTP client.

Podcast enclosures are plain ``http(s)`` files on arbitrary publisher/CDN
hosts, so they are **not** handed to yt-dlp (whose generic extractor would
follow redirects itself, bypassing our SSRF checks). This module does the whole
request under our control:

* every redirect hop is followed manually (max 5), and each hop's host is
  resolved and rejected if it points at a private/loopback/link-local/metadata
  address;
* the connection is made to the **validated** address (a pinned resolver), not
  to a freshly-resolved one, which closes the DNS-rebinding window;
* the response must look like audio (``audio/*`` or ``application/octet-stream``);
* the body is streamed to disk with a hard size cap and a total-time cap;
* no cookies are ever sent (a throwaway ``DummyCookieJar``).

The result is the publisher's original file; it is tagged, never transcoded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from bot.utils import ip_is_public, resolve_host_addresses, sanitize_filename

log = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_ALLOWED_SCHEMES = ("http://", "https://")
_ALLOWED_CONTENT_PREFIXES = ("audio/",)
_ALLOWED_CONTENT_TYPES = ("application/octet-stream", "application/ogg")
_CHUNK = 64 * 1024


class EnclosureError(Exception):
    """Base error for a failed enclosure fetch."""


class EnclosureTooLarge(EnclosureError):
    """The enclosure exceeds the configured size cap."""


class EnclosureBlocked(EnclosureError):
    """The URL or a redirect hop pointed at a non-public address."""


class EnclosureNotFound(EnclosureError):
    """The server did not return a downloadable audio body."""


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """Resolves only to pre-validated addresses (rebinding protection)."""

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self._mapping = mapping

    async def resolve(self, host: str, port: int = 0, family: int = 0):  # noqa: ANN001
        addrs = self._mapping.get(host)
        if not addrs:
            raise OSError(f"no validated address for host {host!r}")
        return [
            {
                "hostname": host,
                "host": ip,
                "port": port or 0,
                "family": socket.AF_INET6 if ":" in ip else socket.AF_INET,
                "proto": 0,
                "flags": 0,
            }
            for ip in addrs
        ]

    async def close(self) -> None:  # pragma: no cover - aiohttp calls this
        return None


def _as_literal(host: str) -> str | None:
    import ipaddress

    try:
        return str(ipaddress.ip_address(host.strip()))
    except ValueError:
        return None


async def _validated_addresses(host: str) -> list[str]:
    """Resolve ``host`` and return only its public addresses (rebinding-safe)."""
    if not host:
        raise EnclosureBlocked("missing host")
    literal = _as_literal(host)
    if literal is not None:
        if not ip_is_public(literal):
            raise EnclosureBlocked(f"host is not public: {host!r}")
        return [literal]
    addresses = await resolve_host_addresses(host)
    if not addresses:
        raise EnclosureBlocked(f"could not resolve host: {host!r}")
    public = [ip for ip in addresses if ip_is_public(ip)]
    if not public:
        raise EnclosureBlocked(f"host {host!r} resolves only to non-public addresses")
    return public


def _join(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location)


def _extension_for(content_type: str | None, url: str) -> str:
    lowered = (content_type or "").lower()
    if "mpeg" in lowered or "mp3" in lowered:
        return ".mp3"
    if "mp4" in lowered or "m4a" in lowered or "aac" in lowered:
        return ".m4a"
    if "ogg" in lowered or "opus" in lowered:
        return ".ogg"
    if "wav" in lowered:
        return ".wav"
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aac"):
        return suffix
    return ".mp3"


def _content_type_allowed(value: str | None) -> bool:
    """Reject obvious non-audio responses (HTML error pages, JSON, …).

    A missing header is tolerated — many CDNs omit it — but a *present* type
    must look like audio, otherwise an error page would be saved as a file.
    """
    if not value:
        return True
    lowered = value.lower().split(";", 1)[0].strip()
    return lowered.startswith(_ALLOWED_CONTENT_PREFIXES) or lowered in _ALLOWED_CONTENT_TYPES


async def stream_enclosure(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int,
    proxy: str | None = None,
    total_timeout: float = 1800.0,
    hop_timeout: float = 30.0,
    filename_hint: str | None = None,
) -> Path:
    """Download ``url`` to ``dest_dir`` and return the written file.

    Raises :class:`EnclosureError` (or a subclass) on any policy violation.
    """
    if not url.lower().startswith(_ALLOWED_SCHEMES):
        raise EnclosureBlocked(f"unsupported scheme: {url!r}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    current = url
    seen: set[str] = set()

    for _hop in range(_MAX_REDIRECTS + 1):
        if current in seen:
            raise EnclosureError("redirect loop")
        seen.add(current)

        split = urlsplit(current)
        if split.scheme not in ("http", "https"):
            raise EnclosureBlocked(f"unsupported scheme: {split.scheme!r}")
        host = split.hostname or ""

        # Validate every hop. With a proxy the egress resolves the name, so we
        # can only assert it is publicly routable; the pin below is what makes
        # the direct case rebinding-safe.
        addresses = await _validated_addresses(host)

        connector_kwargs: dict = {}
        if not proxy:
            connector_kwargs["resolver"] = _PinnedResolver({host: addresses})

        timeout = aiohttp.ClientTimeout(total=total_timeout, connect=hop_timeout, sock_read=hop_timeout)
        jar = aiohttp.DummyCookieJar()  # never stores or sends cookies
        connector = aiohttp.TCPConnector(**connector_kwargs)
        async with aiohttp.ClientSession(
            connector=connector, cookie_jar=jar, timeout=timeout, trust_env=False
        ) as session, session.get(
            current,
            allow_redirects=False,
            proxy=proxy,
            headers={"User-Agent": "Mozilla/5.0 (compatible; savevideo-bot podcast fetcher)"},
        ) as resp:
            status = resp.status
            location = resp.headers.get("Location")

            if status in (301, 302, 303, 307, 308):
                if not location:
                    raise EnclosureError(f"redirect with no location (HTTP {status})")
                current = _join(current, location)
                continue

            if status != 200:
                raise EnclosureNotFound(f"unexpected HTTP status {status}")

            content_type = resp.headers.get("Content-Type")
            if not _content_type_allowed(content_type):
                raise EnclosureError(f"non-audio content type: {content_type!r}")

            declared = _content_length(resp.headers)
            if declared is not None and declared > max_bytes:
                raise EnclosureTooLarge(f"enclosure declares {declared} bytes (cap {max_bytes})")

            name = sanitize_filename(filename_hint or Path(urlsplit(url).path).stem) or "episode"
            dest = dest_dir / f"{name}{_extension_for(content_type, current)}"
            written = await _stream_body(resp, dest, max_bytes)
            log.info("podcast enclosure ok bytes=%d type=%s url=%s", written, content_type, host)
            return dest

    raise EnclosureError(f"too many redirects (>{_MAX_REDIRECTS})")


def _content_length(headers) -> int | None:
    raw = headers.get("Content-Length")
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


async def _stream_body(resp, dest: Path, max_bytes: int) -> int:
    total = 0
    try:
        with dest.open("wb") as handle:
            async for chunk in resp.content.iter_chunked(_CHUNK):
                total += len(chunk)
                if total > max_bytes:
                    raise EnclosureTooLarge(f"enclosure exceeds {max_bytes} bytes")
                handle.write(chunk)
    except EnclosureTooLarge:
        await asyncio.to_thread(_unlink_quietly, dest)
        raise
    except Exception:
        await asyncio.to_thread(_unlink_quietly, dest)
        raise
    if total == 0:
        await asyncio.to_thread(_unlink_quietly, dest)
        raise EnclosureError("empty enclosure body")
    return total


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
