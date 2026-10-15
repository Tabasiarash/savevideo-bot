"""Recognise and normalise Spotify links without any credentials.

Handles the canonical ``open.spotify.com/<kind>/<id>`` form (including the
``/intl-xx/`` locale prefix and ``?si=`` tracking params), the ``play.spotify``
legacy host and ``spotify:<kind>:<id>`` URIs. Short links (``spotify.link``,
``spoti.fi``, ``link.spotify.com``) carry no id, so they must be resolved first
via :func:`resolve_short_link`, which follows redirects but only ever accepts
hosts on the allow-list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .http import fetch

OPEN_HOST = "open.spotify.com"
_SHORT_HOSTS = frozenset({"spotify.link", "spoti.fi", "link.spotify.com"})
_SPOTIFY_HOSTS = frozenset({OPEN_HOST, "play.spotify.com", "open.spotify.com"}) | _SHORT_HOSTS

# Kinds we understand. Collections (album/playlist/show) are gated behind
# SPOTIFY_ENABLE_COLLECTIONS by the handler, not here.
KINDS = frozenset({"track", "album", "playlist", "episode", "show", "artist"})
COLLECTION_KINDS = frozenset({"album", "playlist", "show", "artist"})

_ID_RE = re.compile(r"^[A-Za-z0-9]{16,32}$")


@dataclass(frozen=True)
class SpotifyRef:
    kind: str
    id: str
    canonical: str

    @property
    def is_collection(self) -> bool:
        return self.kind in COLLECTION_KINDS


def _valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value))


def spotify_host(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.lower()


def is_spotify_url(url: str) -> bool:
    text = (url or "").strip()
    if text.lower().startswith("spotify:"):
        return True
    return spotify_host(text) in _SPOTIFY_HOSTS


def is_short_link(url: str) -> bool:
    return spotify_host((url or "").strip()) in _SHORT_HOSTS


def parse_spotify_url(url: str) -> SpotifyRef | None:
    """Parse a direct Spotify URL/URI. Returns ``None`` for short links."""
    text = (url or "").strip()
    if not text:
        return None

    if text.lower().startswith("spotify:"):
        parts = text.split(":")
        if len(parts) >= 3 and parts[1] in KINDS and _valid_id(parts[2]):
            kind, sid = parts[1], parts[2]
            return SpotifyRef(kind, sid, f"https://{OPEN_HOST}/{kind}/{sid}")
        return None

    if spotify_host(text) not in _SPOTIFY_HOSTS or is_short_link(text):
        return None

    segments = [seg for seg in urlsplit(text).path.split("/") if seg]
    if segments and segments[0].startswith("intl-"):
        segments = segments[1:]
    if len(segments) >= 2 and segments[0] in KINDS and _valid_id(segments[1]):
        kind, sid = segments[0], segments[1]
        return SpotifyRef(kind, sid, f"https://{OPEN_HOST}/{kind}/{sid}")
    return None


async def resolve_short_link(
    url: str,
    *,
    proxy: str | None = None,
    max_hops: int = 5,
    timeout: float = 10.0,
) -> SpotifyRef | None:
    """Follow redirects for a short link, staying on Spotify's allow-list."""
    target = (url or "").strip()
    if not is_short_link(target):
        return parse_spotify_url(target)

    for _ in range(max_hops):
        resp = await fetch(target, proxy=proxy, timeout=timeout, allow_redirects=False)
        location = resp.headers.get("location", "")
        if not location:
            break
        if location.startswith("/"):
            location = f"https://{spotify_host(target)}{location}"
        if not is_spotify_url(location):
            return None
        ref = parse_spotify_url(location)
        if ref is not None:
            return ref
        target = location
    return None
