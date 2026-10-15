"""Read public Spotify track metadata (never audio) from credential-free sources.

Provider chain, cheapest first:

1. **oEmbed** (``/oembed?url=...``) — no credentials, gives a clean title and
   cover image.
2. **Page meta tags** — the server-rendered ``og:*`` / ``music:*`` tags add
   artist, album, duration and release year.
3. **Web API** (optional) — only used when ``SPOTIFY_CLIENT_ID``/
   ``SPOTIFY_CLIENT_SECRET`` are set; fills gaps (ISRC, exact duration).

Results are cached in the ``spotify_meta`` table for 24h. Every field is
best-effort and may be ``None``; matching degrades gracefully.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import quote

from .http import fetch, post_form
from .urls import OPEN_HOST, SpotifyRef, is_spotify_api, is_spotify_host

META_TTL_S = 24 * 60 * 60
_API_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com/v1"

# kind -> (endpoint segment, sub-resource, is paginated), for listing a collection.
_COLLECTION_ENDPOINTS = {
    "album": ("albums", "tracks"),
    "playlist": ("playlists", "tracks"),
    "show": ("shows", "episodes"),
}

_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"([a-zA-Z:_-]+)\s*=\s*[\"']([^\"']*)[\"']")
_OG_TITLE_BY = re.compile(
    r"^(?P<title>.+?)\s+-\s+(?:song and lyrics by|song by|album by|single by|ep by|"
    r"compilation by|playlist by)\s+(?P<artist>.+?)(?:\s*\|\s*Spotify)?$",
    re.I,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


@dataclass
class TrackMeta:
    spotify_id: str
    kind: str
    title: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    duration_s: float | None = None
    cover_url: str | None = None
    year: str | None = None
    isrc: str | None = None
    provider: str = ""

    def is_usable(self) -> bool:
        return bool(self.title)

    def artist_line(self) -> str:
        return ", ".join(self.artists)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> TrackMeta | None:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return cls(
            spotify_id=str(data.get("spotify_id", "")),
            kind=str(data.get("kind", "")),
            title=data.get("title"),
            artists=list(data.get("artists") or []),
            album=data.get("album"),
            duration_s=data.get("duration_s"),
            cover_url=data.get("cover_url"),
            year=data.get("year"),
            isrc=data.get("isrc"),
            provider=str(data.get("provider", "")),
        )


def _meta_tags(html: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for tag in _TAG_RE.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        key = attrs.get("property") or attrs.get("name")
        if key and "content" in attrs:
            tags.setdefault(key.lower(), attrs["content"])
    return tags


def _merge(base: TrackMeta, extra: TrackMeta) -> TrackMeta:
    if not base.title and extra.title:
        base.title = extra.title
    if not base.artists and extra.artists:
        base.artists = extra.artists
    if not base.album and extra.album:
        base.album = extra.album
    if base.duration_s is None and extra.duration_s is not None:
        base.duration_s = extra.duration_s
    if not base.cover_url and extra.cover_url:
        base.cover_url = extra.cover_url
    if not base.year and extra.year:
        base.year = extra.year
    if not base.isrc and extra.isrc:
        base.isrc = extra.isrc
    if extra.provider and extra.provider not in base.provider:
        base.provider = f"{base.provider}+{extra.provider}".strip("+")
    return base


def from_oembed(payload: dict, ref: SpotifyRef) -> TrackMeta:
    return TrackMeta(
        spotify_id=ref.id,
        kind=ref.kind,
        title=(payload.get("title") or "").strip() or None,
        cover_url=payload.get("thumbnail_url") or None,
        provider="oembed",
    )


def from_page(html: str, ref: SpotifyRef) -> TrackMeta:
    tags = _meta_tags(html)
    meta = TrackMeta(spotify_id=ref.id, kind=ref.kind, provider="page")

    title = tags.get("og:title", "")
    match = _OG_TITLE_BY.match(title)
    if match:
        meta.title = match.group("title").strip() or None
        artist = match.group("artist").strip()
        if artist:
            meta.artists = [artist]
    elif title:
        meta.title = re.sub(r"\s*\|\s*Spotify\s*$", "", title).strip() or None

    description = tags.get("og:description", "")
    if description and not meta.artists:
        head = re.sub(r"^(?:Listen to|Play|Preview)\b.*?\bon Spotify\.\s*", "", description, flags=re.I)
        parts = [p.strip() for p in re.split(r"[·|]", head) if p.strip()]
        if parts:
            meta.artists = [parts[0]]

    if description and meta.year is None:
        year = _YEAR_RE.search(description)
        if year:
            meta.year = year.group(0)

    duration = tags.get("music:duration", "")
    if duration.isdigit():
        meta.duration_s = float(duration)

    meta.album = tags.get("music:album") or None
    meta.cover_url = tags.get("og:image") or None
    return meta


def from_api(payload: dict, ref: SpotifyRef) -> TrackMeta:
    meta = TrackMeta(spotify_id=ref.id, kind=ref.kind, provider="api")
    meta.title = (payload.get("name") or "").strip() or None
    meta.artists = [a.get("name") for a in payload.get("artists") or [] if a.get("name")]
    album = payload.get("album") or {}
    meta.album = album.get("name")
    images = album.get("images") or payload.get("images") or []
    if images:
        meta.cover_url = images[0].get("url")
    duration_ms = payload.get("duration_ms")
    if isinstance(duration_ms, (int, float)):
        meta.duration_s = round(duration_ms / 1000.0, 2)
    release = album.get("release_date") or payload.get("release_date") or ""
    if release[:4].isdigit():
        meta.year = release[:4]
    meta.isrc = (payload.get("external_ids") or {}).get("isrc")
    return meta


async def _from_oembed(ref: SpotifyRef, proxy: str | None) -> TrackMeta | None:
    url = f"https://{OPEN_HOST}/oembed?url={quote(ref.canonical, safe='')}"
    resp = await fetch(url, proxy=proxy, allow_redirects=True, is_allowed=is_spotify_host)
    if resp.status != 200:
        return None
    try:
        payload = json.loads(resp.text)
    except (TypeError, ValueError):
        return None
    return from_oembed(payload, ref) if isinstance(payload, dict) else None


async def _from_page(ref: SpotifyRef, proxy: str | None) -> TrackMeta | None:
    resp = await fetch(ref.canonical, proxy=proxy, allow_redirects=True, is_allowed=is_spotify_host)
    if resp.status != 200 or not resp.text:
        return None
    return from_page(resp.text, ref)


async def _api_token(client_id: str, client_secret: str, proxy: str | None) -> str | None:
    resp = await post_form(
        _API_TOKEN_URL,
        {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        proxy=proxy,
    )
    if resp.status != 200:
        return None
    try:
        return (json.loads(resp.text) or {}).get("access_token")
    except (TypeError, ValueError):
        return None


async def _from_api(ref: SpotifyRef, client_id: str, client_secret: str, proxy: str | None) -> TrackMeta | None:
    token = await _api_token(client_id, client_secret, proxy)
    if not token:
        return None
    resp = await fetch(
        f"{_API_BASE}/{ref.kind}s/{ref.id}",
        proxy=proxy,
        allow_redirects=True,
        is_allowed=is_spotify_api,
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status != 200:
        return None
    try:
        payload = json.loads(resp.text)
    except (TypeError, ValueError):
        return None
    return from_api(payload, ref) if isinstance(payload, dict) else None


async def fetch_metadata(
    db,
    ref: SpotifyRef,
    *,
    proxy: str | None = None,
    client_id: str = "",
    client_secret: str = "",
    use_cache: bool = True,
) -> TrackMeta | None:
    """Return best-effort metadata for ``ref``, using the 24h cache when enabled."""
    if use_cache:
        cached = await db.spotify_meta_get(ref.id, META_TTL_S)
        if cached:
            meta = TrackMeta.from_json(cached)
            if meta and meta.is_usable():
                return meta

    merged: TrackMeta | None = None
    for provider in (
        lambda: _from_oembed(ref, proxy),
        lambda: _from_page(ref, proxy),
    ):
        try:
            part = await provider()
        except Exception:
            part = None
        if part is not None:
            merged = part if merged is None else _merge(merged, part)

    if merged is not None and merged.is_usable() and (client_id and client_secret) and not merged.isrc:
        try:
            api = await _from_api(ref, client_id, client_secret, proxy)
        except Exception:
            api = None
        if api is not None:
            merged = _merge(merged, api)

    if merged is None or not merged.is_usable():
        return merged if merged is not None else None

    if use_cache and merged is not None:
        with contextlib.suppress(Exception):
            await db.spotify_meta_put(ref.id, ref.kind, merged.to_json())
    return merged


def _entry_to_ref(kind: str, entry: dict) -> SpotifyRef | None:
    item = entry.get("track") or entry.get("item") or entry
    if not isinstance(item, dict):
        return None
    sid = item.get("id")
    item_kind = item.get("type") or ("episode" if kind == "show" else "track")
    if not sid:
        return None
    return SpotifyRef(str(item_kind), str(sid), f"https://{OPEN_HOST}/{item_kind}/{sid}")


async def list_collection_tracks(
    db,
    ref: SpotifyRef,
    *,
    proxy: str | None = None,
    client_id: str = "",
    client_secret: str = "",
    limit: int = 25,
) -> list[SpotifyRef]:
    """Resolve an album/playlist/show (or artist top tracks) to track refs.

    Requires the Web API (client credentials); returns ``[]`` when they are
    missing or the API rejects the request — the caller then tells the user.
    """
    if limit <= 0 or not (client_id and client_secret):
        return []
    token = await _api_token(client_id, client_secret, proxy)
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}

    if ref.kind == "artist":
        resp = await fetch(
            f"{_API_BASE}/artists/{ref.id}/top-tracks?market=US",
            proxy=proxy,
            allow_redirects=True,
            is_allowed=is_spotify_api,
            headers=headers,
        )
        if resp.status != 200:
            return []
        try:
            data = json.loads(resp.text)
        except (TypeError, ValueError):
            return []
        refs = [r for item in (data.get("tracks") or [])[:limit] if (r := _entry_to_ref("artist", item))]
        return refs

    endpoint = _COLLECTION_ENDPOINTS.get(ref.kind)
    if endpoint is None:
        return []
    path, sub = endpoint
    url = f"{_API_BASE}/{path}/{ref.id}/{sub}?limit=50"
    out: list[SpotifyRef] = []
    while url and len(out) < limit:
        resp = await fetch(url, proxy=proxy, allow_redirects=True, is_allowed=is_spotify_api, headers=headers)
        if resp.status != 200:
            break
        try:
            data = json.loads(resp.text)
        except (TypeError, ValueError):
            break
        for item in data.get("items") or []:
            resolved = _entry_to_ref(ref.kind, item)
            if resolved is not None:
                out.append(resolved)
                if len(out) >= limit:
                    break
        url = (data.get("next") or "") if len(out) < limit else ""
    return out[:limit]
