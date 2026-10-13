"""Pure helpers: URL extraction/normalisation, SSRF guards, formatting.

Nothing in this module touches the network unless it is explicitly async and is
called for that purpose (``validate_url_safety`` / ``validate_redirect_chain``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from math import ceil
from typing import Iterable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"']+")

# Query parameters that are pure tracking noise and are stripped for cache keys.
_TRACKING_PARAMS = {
    "si",
    "igshid",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "spm",
    "yclid",
    "ruclid",
    "ref_src",
    "ref_url",
    "oid",
    "irclickid",
}

# Leading subdomains that are stripped for canonical cache keys.
_STRIP_HOST_PREFIXES = ("www.", "web.", "m.", "mobile.")

# Hosts whose extractors resolve internally; we skip the extra redirect probe.
_KNOWN_EXTRACTOR_HOSTS = {
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "fb.watch",
    "reddit.com",
    "redd.it",
    "vimeo.com",
    "pinterest.com",
    "pinterest.de",
    "soundcloud.com",
    "twitch.tv",
    "vk.com",
    "dailymotion.com",
    "bilibili.com",
    "tumblr.com",
    "rumble.com",
    "odysee.com",
}


class URLBlockedError(ValueError):
    """Raised when a URL resolves to a private/reserved address."""


class URLParseError(ValueError):
    """Raised when a string is not a usable http(s) URL."""


def extract_urls(text: str) -> list[str]:
    """Find all http(s) URLs in ``text``, cleaning trailing punctuation."""
    found: list[str] = []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(".,;:!?'\"<>")
        while (
            (url.endswith(")") and url.count(")") > url.count("("))
            or (url.endswith("]") and url.count("]") > url.count("["))
            or (url.endswith("}") and url.count("}") > url.count("{"))
        ):
            url = url[:-1]
        found.append(url)
    return found


def _canonical_host(host: str) -> str:
    host = host.lower()
    for prefix in _STRIP_HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    return host


def normalize_url(url: str) -> str:
    """Normalize a URL into a canonical cache key form.

    - lowercases scheme/host and strips default ports
    - strips tracking parameters (``utm_*``, ``si``, ``igshid``, …)
    - expands ``youtu.be`` links
    - strips ``www.`` / ``m.`` style prefixes
    """
    scheme, netloc, path, query, fragment = urlsplit(url.strip())
    if scheme.lower() not in ("http", "https"):
        raise URLParseError(f"unsupported scheme: {scheme!r}")

    scheme = scheme.lower()
    host = netloc
    port = ""
    if "[" in host:  # ipv6 literal
        host, _, rest = host.partition("]")
        host = host[1:]
        if rest.startswith(":"):
            port = rest[1:]
    elif ":" in host:
        host, _, port = host.rpartition(":")

    host = _canonical_host(host)

    if host == "youtu.be" and port == "":
        if path.startswith("/"):
            vid = path[1:].split("?")[0].split("/")[0]
            if vid:
                host = "youtube.com"
                path = "/watch"
                query = f"v={vid}"

    if port:
        default_port = "443" if scheme == "https" else "80"
        if port != default_port:
            host = f"{host}:{port}"

    kept = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    query = "&".join(f"{k}={v}" for k, v in kept)

    return urlunsplit((scheme, host, path or "", query, ""))


def hostname_from_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return _canonical_host(host)


def site_from_url(url: str) -> str:
    """Human readable site name for stats, derived from the host."""
    host = hostname_from_url(url)
    if not host:
        return "unknown"
    domains = ("youtube.com", "instagram.com", "tiktok.com", "twitter.com",
               "x.com", "facebook.com", "reddit.com", "vimeo.com", "pinterest.com",
               "soundcloud.com", "twitch.tv", "dailymotion.com", "vk.com", "ok.ru")
    for d in domains:
        if host == d or host.endswith("." + d):
            return d.split(".")[0]
    # A very common edge case: translate.lol / clip.cafe clones etc. stay generic.
    return host


def is_supported_scheme(url: str) -> bool:
    return urlsplit(url).scheme.lower() in ("http", "https")


# --------------------------------------------------------------------------
# SSRF protection
# --------------------------------------------------------------------------

def ip_is_public(ip: str) -> bool:
    """True only for globally routable IPs (rejects private/reserved/link-local)."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return bool(addr.is_global)


def host_is_public_literal(host: str) -> bool:
    """Check a host that is already an IP literal (no DNS involved)."""
    try:
        return bool(ipaddress.ip_address(host.strip()).is_global)
    except ValueError:
        return True  # not an IP literal — ambiguous, resolve via DNS


def _host_endswith_local(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    return lowered in ("localhost",) or lowered.endswith(".local") or lowered.endswith(".internal")


async def resolve_host_addresses(host: str) -> list[str]:
    """Resolve a hostname to IPs off the event loop (thread-pool DNS)."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except OSError:
        return []
    seen: dict[str, None] = {}
    for info in infos:
        addr = info[4][0]
        seen.setdefault(addr, None)
    return list(seen)


async def check_url_safety(url: str) -> None:
    """Raise :class:`URLBlockedError` if ``url`` would reach a private address.

    For literal-IP hosts the decision is purely local; hostnames are resolved
    with the system resolver. This is called before any user-controlled URL is
    handed to yt-dlp.
    """
    if not is_supported_scheme(url):
        raise URLBlockedError("unsupported scheme")
    host = urlsplit(url).hostname or ""
    if _host_endswith_local(host):
        raise URLBlockedError(f"host is local: {host!r}")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addresses = await resolve_host_addresses(host)
        if not addresses:
            raise URLBlockedError(f"could not resolve host: {host!r}")
        for addr in addresses:
            if not ip_is_public(addr):
                raise URLBlockedError(f"host {host!r} resolves to non-public address {addr!r}")
    else:
        if not addr.is_global:
            raise URLBlockedError(f"address is not public: {host!r}")


async def validate_redirect_chain(url: str, timeout: float = 5.0) -> None:
    """Follow redirects for *generic* hosts and abort on private targets.

    Known extractor sites (YouTube, TikTok, …) are skipped because their yt-dlp
    extractors handle redirects internally to public CDNs. This gives best-effort
    SSRF coverage after redirects ("where feasible"). Network failures are
    non-fatal: yt-dlp itself is the ultimate guard and we log a warning.
    """
    host = hostname_from_url(url)
    if host in _KNOWN_EXTRACTOR_HOSTS or any(host.endswith("." + d) for d in _KNOWN_EXTRACTOR_HOSTS):
        return
    try:
        import aiohttp

        current = url
        seen: set[str] = set()
        async with aiohttp.ClientSession() as session:
            for _ in range(6):
                if current in seen:
                    return
                seen.add(current)
                async with session.get(
                    current, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status not in (301, 302, 303, 307, 308):
                        return
                    location = resp.headers.get("Location")
                    if not location:
                        return
                    current = _join(current, location)
                    await check_url_safety(current)
    except URLBlockedError:
        raise
    except Exception as exc:  # noqa: BLE001 — best-effort guard
        log.warning("redirect safety probe failed for %s: %s", host, exc)


def _join(base: str, location: str) -> str:
    if location.startswith(("http://", "https://")):
        return location
    from urllib.parse import urljoin

    return urljoin(base, location)


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def format_size(size_bytes: int | None) -> str:
    if size_bytes is None or size_bytes < 0:
        return "?"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return "?"


def format_duration(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return "?"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def sanitize_filename(name: str, max_len: int = 180) -> str:
    """Make ``name`` safe as a single filename on Linux/Darwin/Windows."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", name.strip())
    cleaned = cleaned.replace("\n", " ").replace("\r", " ").strip(". ")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        cleaned = "download"
    return cleaned[:max_len].rstrip(". ")


def target_bitrate_bps(size_limit_bytes: int, duration_s: float) -> int:
    """Bitrate (bits/s) so the container stays under ``size_limit_bytes``."""
    if duration_s <= 0:
        return 0
    # ~5% container overhead / muxing allowance.
    return int(size_limit_bytes * 8 * 0.95 / duration_s)


def parts_for_size(size_bytes: int, limit_bytes: int) -> int:
    """How many parts a file of ``size_bytes`` splits into given a per-part limit."""
    if size_bytes <= 0 or limit_bytes <= 0:
        return 1
    return max(1, ceil(size_bytes / limit_bytes))


def part_seconds(duration_s: float, part: int) -> float:
    """Duration of ``part`` (1-indexed) when a file is split evenly."""
    if duration_s <= 0:
        return duration_s
    return duration_s / part


def iter_hosts(urls: Iterable[str]) -> str:
    """Join the canonical hosts of several URLs for logging."""
    return ", ".join(hostname_from_url(u) for u in urls)