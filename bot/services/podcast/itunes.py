"""Locate a podcast episode's publisher audio through Apple's iTunes Search API.

The public ``search`` endpoint is credential-free and, when queried with
``media=podcast&entity=podcastEpisode``, returns the **publisher's own**
``episodeUrl`` (the RSS enclosure) for a matching episode — the same file a
podcast app downloads. No Spotify audio is involved.

This is shared infrastructure, so it is treated politely: results are cached,
and a 403/429 response sets a per-storefront cooldown that is honoured by every
request until it lapses. Searches run across ``PODCAST_REGIONS`` in order,
because a show may only be listed in its home storefront.

Privacy note: the episode title and show name are sent to Apple's iTunes
Search API.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass

from bot.services.spotify.http import fetch

from .meta import plausible_show
from .scoring import score_candidate

log = logging.getLogger(__name__)

_SEARCH_URL = "https://itunes.apple.com/search"
_RATE_LIMIT_STATUSES = frozenset({403, 429})


@dataclass
class ItunesEpisode:
    title: str | None = None
    show: str | None = None
    duration_s: float | None = None
    audio_url: str | None = None
    feed_url: str | None = None
    artwork_url: str | None = None
    released: str | None = None
    guid: str | None = None
    region: str = ""
    score: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _num(value: object) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _from_result(item: dict, region: str) -> ItunesEpisode | None:
    audio_url = item.get("episodeUrl") or item.get("previewUrl")
    if not isinstance(audio_url, str) or not audio_url.startswith(("http://", "https://")):
        return None
    release = item.get("releaseDate")
    release = release[:10] if isinstance(release, str) and len(release) >= 10 else None
    return ItunesEpisode(
        title=(item.get("trackName") or "").strip() or None,
        show=(item.get("collectionName") or "").strip() or None,
        duration_s=_num(item.get("trackTimeMillis")),
        audio_url=audio_url,
        feed_url=item.get("feedUrl") or None,
        artwork_url=item.get("artworkUrl600") or item.get("artworkUrl60") or None,
        released=release,
        guid=(item.get("episodeGuid") or None),
        region=region,
    )


def _normalise_duration(value: float | None) -> float | None:
    """iTunes reports ``trackTimeMillis`` in milliseconds; store seconds."""
    if value is None:
        return None
    return round(value / 1000.0, 2) if value > 10_000 else value


def score_itunes(meta, cand: ItunesEpisode, settings) -> float:
    return score_candidate(
        title=meta.title,
        show=meta.show,
        duration_s=meta.duration_s,
        cand_title=cand.title,
        cand_show=cand.show,
        cand_duration_s=cand.duration_s,
        tol_min_s=settings.podcast_duration_tol_min_s,
        tol_pct=settings.podcast_duration_tol_pct,
    )


async def _search_region(
    db,
    meta,
    region: str,
    *,
    proxy: str | None,
    settings,
) -> list[ItunesEpisode]:
    term = None
    if plausible_show(meta.show) and meta.title:
        term = f"{meta.show} {meta.title}"
    if not term and meta.title:
        term = meta.title
    if not term:
        return []
    term = " ".join(term.split()).strip()

    url = (
        f"{_SEARCH_URL}?media=podcast&entity=podcastEpisode"
        f"&limit=25&country={region.upper()}&term={term}"
    )
    try:
        resp = await fetch(url, proxy=proxy, timeout=15.0, max_bytes=5 * 1024 * 1024)
    except Exception:  # noqa: BLE001 - a network blip must not abort the search
        log.info("itunes search failed region=%s", region, exc_info=True)
        return []

    if resp.status in _RATE_LIMIT_STATUSES:
        until = int(time.time()) + settings.podcast_itunes_cooldown_s
        try:
            await db.podcast_itunes_block(region, until)
        except Exception:  # noqa: BLE001
            log.warning("could not record itunes cooldown region=%s", region, exc_info=True)
        log.info("itunes rate-limited region=%s; cooling down %ss", region, settings.podcast_itunes_cooldown_s)
        return []

    if resp.status != 200 or not resp.text:
        return []

    try:
        payload = json.loads(resp.text)
    except (TypeError, ValueError):
        return []

    out: list[ItunesEpisode] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        cand = _from_result(item, region)
        if cand is None:
            continue
        cand.duration_s = _normalise_duration(cand.duration_s)
        cand.score = score_itunes(meta, cand, settings)
        out.append(cand)
    return out


async def search_episode(db, meta, *, proxy: str | None, settings) -> list[ItunesEpisode]:
    """Scored, best-first publisher matches across the configured storefronts.

    Storefronts on cooldown are skipped; a caller that needs a result can still
    fall back to the RSS feed or the matcher.
    """
    candidates: list[ItunesEpisode] = []
    now = int(time.time())

    for region in settings.podcast_regions_list():
        try:
            blocked_until = await db.podcast_itunes_blocked_until(region)
        except Exception:  # noqa: BLE001
            blocked_until = 0
        if blocked_until > now:
            continue

        try:
            candidates.extend(await _search_region(db, meta, region, proxy=proxy, settings=settings))
        except Exception:  # noqa: BLE001 - never let one storefront kill the flow
            log.info("itunes search error region=%s", region, exc_info=True)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates
