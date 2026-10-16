"""Download a podcast episode's enclosure (the publisher's own audio file).

The enclosure URL comes from third-party data (an RSS feed or iTunes), so it is
treated as untrusted:

* the fetch runs through the bot's own HTTP client (curl_cffi, the same one used
  for Spotify metadata) with a browser fingerprint — many podcast CDNs (e.g. the
  CloudFront host behind Spreaker) reject clients that do not present one;
* redirects are followed **manually** (max 5). Every hop's host is resolved and
  rejected if it points at a private / loopback / link-local / metadata address,
  and the connection is pinned to the **validated** address via
  ``CurlOpt.RESOLVE`` so a second, attacker-controlled DNS answer (rebinding)
  cannot be used to reach an internal host;
* the response must look like audio (``audio/*`` or octet-stream), the body is
  streamed to disk under a byte cap and a total-time budget, and the partial
  file is removed on any failure;
* cookies are never sent and no proxy environment variables are read.

The result is the publisher's original file; it is tagged, never transcoded.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import curl_cffi.requests as cffi
from curl_cffi.const import CurlOpt
from curl_cffi.requests import exceptions as cffi_errors

from bot.utils import ip_is_public, sanitize_filename

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_AUDIO_PREFIX = "audio/"
_OCTET_TYPES = frozenset({"application/octet-stream", "application/ogg"})
_IMPERSONATE = "chrome"
_USER_AGENT = "Mozilla/5.0 (compatible; savevideo-bot/1.0)"


class EnclosureError(Exception):
    """Base class for enclosure download failures."""


class EnclosureTooLarge(EnclosureError):
    """The enclosure is larger than the configured cap."""


class EnclosureBlocked(EnclosureError):
    """A hop resolved to a non-public address, or the scheme is unsupported."""


class EnclosureNotFound(EnclosureError):
    """The server did not return a downloadable audio body."""


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
    """Download ``url`` into ``dest_dir`` and return the written file's path.

    ``filename_hint`` (e.g. the episode title) is used for the file name when
    given; otherwise the URL's last path segment is used.

    Raises an :class:`EnclosureError` subclass on failure; the partial file (if
    any) is always removed.
    """
    if not url.startswith(("http://", "https://")):
        raise EnclosureBlocked(f"unsupported scheme: {url!r}")
    return await asyncio.to_thread(
        _download,
        url,
        dest_dir,
        max_bytes=max_bytes,
        proxy=proxy,
        total_timeout=max(1.0, total_timeout),
        hop_timeout=max(1.0, hop_timeout),
        filename_hint=filename_hint,
    )


def _download(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int,
    proxy: str | None,
    total_timeout: float,
    hop_timeout: float,
    filename_hint: str | None,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + total_timeout
    proxies = {"http": proxy, "https": proxy} if proxy else None
    seen: set[str] = set()
    current = url

    for _ in range(_MAX_REDIRECTS + 1):
        if current in seen:
            raise EnclosureError("redirect loop")
        seen.add(current)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EnclosureError("total time budget exceeded")

        parts = urlsplit(current)
        if parts.scheme not in ("http", "https"):
            raise EnclosureBlocked(f"unsupported scheme: {parts.scheme!r}")
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = _resolve_public(host)

        # Pin the connection to the address we just validated, unless a proxy
        # (which does its own resolution) is in use. The check above stands.
        options = None
        if not proxy:
            options = {CurlOpt.RESOLVE: [f"{host}:{port}:{ip}" for ip in addresses]}

        with cffi.Session(impersonate=_IMPERSONATE, curl_options=options) as session:
            try:
                resp = session.get(
                    current,
                    allow_redirects=False,
                    stream=True,
                    timeout=(min(hop_timeout, remaining), remaining),
                    proxies=proxies,
                    discard_cookies=True,
                    headers={"User-Agent": _USER_AGENT},
                )
            except cffi_errors.RequestException as exc:
                raise EnclosureError(f"download failed: {exc}") from exc
            try:
                status = resp.status_code
                if status in _REDIRECT_STATUSES:
                    location = resp.headers.get("location")
                    if not location:
                        raise EnclosureError(f"redirect with no location (HTTP {status})")
                    current = urljoin(current, location)
                    continue
                try:
                    return _save_body(
                        resp, current, dest_dir, max_bytes, deadline, filename_hint
                    )
                except cffi_errors.RequestException as exc:
                    raise EnclosureError(f"download failed: {exc}") from exc
            finally:
                with contextlib.suppress(Exception):
                    resp.close()

    raise EnclosureError(f"too many redirects (> {_MAX_REDIRECTS})")


def _save_body(
    resp,
    url: str,
    dest_dir: Path,
    max_bytes: int,
    deadline: float,
    filename_hint: str | None,
) -> Path:
    status = resp.status_code
    if status != 200:
        raise EnclosureNotFound(f"unexpected HTTP {status}")

    content_type = resp.headers.get("content-type")
    if not _content_type_allowed(content_type):
        raise EnclosureError(f"non-audio content type: {content_type!r}")

    declared = _content_length(resp.headers.get("content-length"))
    if declared is not None and declared > max_bytes:
        raise EnclosureTooLarge(f"enclosure declares {declared} bytes")

    ext = _extension_for(content_type, url)
    stem = filename_hint or Path(urlsplit(url).path).stem
    name = sanitize_filename(stem) or "episode"
    dest = dest_dir / f"{name}{ext}"
    written = 0
    try:
        with dest.open("wb") as handle:
            for chunk in resp.iter_content():
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    raise EnclosureTooLarge(f"enclosure exceeds {max_bytes} bytes")
                if time.monotonic() > deadline:
                    raise EnclosureError("total time budget exceeded")
                handle.write(chunk)
    except BaseException:
        _unlink_quietly(dest)
        raise
    if written == 0:
        _unlink_quietly(dest)
        raise EnclosureError("empty enclosure body")
    return dest


def _resolve_public(host: str) -> list[str]:
    """Return public addresses for ``host`` or raise :class:`EnclosureBlocked`."""
    if not host:
        raise EnclosureBlocked("missing host")
    literal = _as_address(host)
    if literal is not None:
        if not ip_is_public(literal):
            raise EnclosureBlocked(f"host is not public: {host!r}")
        return [literal]
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise EnclosureBlocked(f"could not resolve host: {host!r}") from exc
    public = sorted({info[4][0] for info in infos if ip_is_public(info[4][0])})
    if not public:
        raise EnclosureBlocked(f"host {host!r} resolves only to non-public addresses")
    return public


def _as_address(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host.strip()))
    except ValueError:
        return None


def _content_type_allowed(value: str | None) -> bool:
    """Accept audio, a generic octet-stream, or a missing type.

    A *present* non-audio type (an HTML/XML error page) is rejected so an error
    body is never saved as an episode.
    """
    if not value:
        return True
    lowered = value.lower().split(";", 1)[0].strip()
    return lowered.startswith(_AUDIO_PREFIX) or lowered in _OCTET_TYPES


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value.strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _extension_for(content_type: str | None, url: str) -> str:
    lowered = (content_type or "").lower()
    if "mpeg" in lowered or "mp3" in lowered:
        return ".mp3"
    if any(token in lowered for token in ("mp4", "m4a", "aac")):
        return ".m4a"
    if any(token in lowered for token in ("ogg", "opus")):
        return ".ogg"
    if "wav" in lowered:
        return ".wav"
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aac"}:
        return suffix
    return ".mp3"


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
