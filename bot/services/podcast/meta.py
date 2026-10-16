"""Read public metadata for a Spotify podcast episode (never audio).

The episode page itself is client-rendered and exposes no usable ``og:*`` tags,
so the sources here are:

1. **oEmbed** (``/oembed?url=...``) — credential-free, gives a clean title and
   the cover image.
2. **The embed page** (``/embed/episode/<id>``) — a ``__NEXT_DATA__`` JSON blob
   carries the show name (``subtitle``) and exact duration in ms. This is
   **optional**: if it changes or disappears, the oEmbed title plus the iTunes
   search still resolve the episode.

Results are cached in ``podcast_meta`` for 24h. Every field is best-effort;
matching degrades gracefully when one is missing.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import quote

from bot.services.spotify.http import fetch
from bot.services.spotify.urls import OPEN_HOST, SpotifyRef, is_spotify_host

META_TTL_S = 24 * 60 * 60

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.S,
)


@dataclass
class EpisodeMeta:
    spotify_id: str
    kind: str = "episode"
    title: str | None = None
    show: str | None = None
    duration_s: float | None = None
    cover_url: str | None = None
    feed_url: str | None = None
    released: str | None = None
    provider: str = ""

    def is_usable(self) -> bool:
        return bool(self.title)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> EpisodeMeta | None:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return cls(
            spotify_id=str(data.get("spotify_id", "")),
            kind=str(data.get("kind", "episode")),
            title=data.get("title"),
            show=data.get("show"),
            duration_s=data.get("duration_s"),
            cover_url=data.get("cover_url"),
            feed_url=data.get("feed_url"),
            released=data.get("released"),
            provider=str(data.get("provider", "")),
        )


def from_oembed(payload: dict, ref: SpotifyRef) -> EpisodeMeta:
    return EpisodeMeta(
        spotify_id=ref.id,
        title=(payload.get("title") or "").strip() or None,
        cover_url=payload.get("thumbnail_url") or None,
        provider="oembed",
    )


def from_embed_page(html: str, ref: SpotifyRef) -> EpisodeMeta:
    """Best-effort parse of the embed page's ``__NEXT_DATA__`` blob.

    Only the show name and duration are taken from here; both are optional.
    """
    meta = EpisodeMeta(spotify_id=ref.id, provider="embed")
    match = _NEXT_DATA_RE.search(html or "")
    if not match:
        return meta
    try:
        blob = json.loads(match.group(1))
    except (TypeError, ValueError):
        return meta

    entity = blob.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
    if not isinstance(entity, dict):
        return meta

    subtitle = entity.get("subtitle")
    if isinstance(subtitle, str) and subtitle.strip():
        meta.show = subtitle.strip()

    duration_ms = entity.get("duration")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        meta.duration_s = round(duration_ms / 1000.0, 2)

    release = entity.get("releaseDate")
    if isinstance(release, dict):
        release = release.get("isoString") or release.get("human") or None
    if isinstance(release, str) and len(release) >= 4:
        meta.released = release[:10]

    return meta


def _merge(base: EpisodeMeta, extra: EpisodeMeta) -> EpisodeMeta:
    if not base.title and extra.title:
        base.title = extra.title
    if not base.show and extra.show:
        base.show = extra.show
    if base.duration_s is None and extra.duration_s is not None:
        base.duration_s = extra.duration_s
    if not base.cover_url and extra.cover_url:
        base.cover_url = extra.cover_url
    if not base.feed_url and extra.feed_url:
        base.feed_url = extra.feed_url
    if not base.released and extra.released:
        base.released = extra.released
    if extra.provider and extra.provider not in base.provider:
        base.provider = f"{base.provider}+{extra.provider}".strip("+")
    return base


async def _from_oembed(ref: SpotifyRef, proxy: str | None) -> EpisodeMeta | None:
    url = f"https://{OPEN_HOST}/oembed?url={quote(ref.canonical, safe='')}"
    resp = await fetch(url, proxy=proxy, allow_redirects=True, is_allowed=is_spotify_host)
    if resp.status != 200:
        return None
    try:
        payload = json.loads(resp.text)
    except (TypeError, ValueError):
        return None
    return from_oembed(payload, ref) if isinstance(payload, dict) else None


async def _from_embed_page(ref: SpotifyRef, proxy: str | None) -> EpisodeMeta | None:
    url = f"https://{OPEN_HOST}/embed/{ref.kind}/{ref.id}"
    resp = await fetch(url, proxy=proxy, allow_redirects=True, is_allowed=is_spotify_host)
    if resp.status != 200 or not resp.text:
        return None
    return from_embed_page(resp.text, ref)


async def fetch_metadata(db, ref: SpotifyRef, *, proxy: str | None = None) -> EpisodeMeta | None:
    """Return best-effort episode metadata, using the 24h cache when available."""
    cached = await db.podcast_meta_get(ref.id, META_TTL_S)
    if cached:
        meta = EpisodeMeta.from_json(cached)
        if meta and meta.is_usable():
            return meta

    merged: EpisodeMeta | None = None
    for provider in (
        lambda: _from_oembed(ref, proxy),
        lambda: _from_embed_page(ref, proxy),
    ):
        try:
            part = await provider()
        except Exception:  # noqa: BLE001 - a source failing must not abort
            part = None
        if part is not None:
            merged = part if merged is None else _merge(merged, part)

    if merged is None or not merged.is_usable():
        return merged

    with contextlib.suppress(Exception):
        await db.podcast_meta_put(ref.id, merged.to_json())
    return merged
