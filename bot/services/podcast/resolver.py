"""Resolve an episode to downloadable audio, in a fixed priority order.

1. **iTunes Search** — the publisher's own ``episodeUrl`` for the episode.
2. **RSS enclosures** — from the show's feed (via iTunes's ``feedUrl``), which
   also works when iTunes lists the show but not that specific episode.
3. **YouTube/SoundCloud match** — a last resort, only when no publisher copy
   exists (typically a Spotify-exclusive). It is **labelled** to the user,
   because that copy may differ from the episode, and it is held to a stricter
   score than a publisher match.

The result is cached so a rematch does not re-query the sources.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import asdict, dataclass

from bot.services.spotify import matcher as music_matcher
from bot.services.spotify.matcher import Candidate

from .feed import list_enclosures
from .itunes import ItunesEpisode, search_episode
from .scoring import score_candidate

log = logging.getLogger(__name__)

RESOLVED_TTL_S = 24 * 60 * 60


@dataclass
class ResolvedEpisode:
    audio_url: str
    source: str  # itunes / rss / youtube / soundcloud
    title: str | None = None
    show: str | None = None
    duration_s: float | None = None
    artwork_url: str | None = None
    feed_url: str | None = None
    is_fallback: bool = False  # True for a matched (non-publisher) copy

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> ResolvedEpisode | None:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("audio_url"):
            return None
        return cls(
            audio_url=str(data["audio_url"]),
            source=str(data.get("source", "")),
            title=data.get("title"),
            show=data.get("show"),
            duration_s=data.get("duration_s"),
            artwork_url=data.get("artwork_url"),
            feed_url=data.get("feed_url"),
            is_fallback=bool(data.get("is_fallback")),
        )


def _from_itunes(cand: ItunesEpisode, meta) -> ResolvedEpisode:
    return ResolvedEpisode(
        audio_url=cand.audio_url or "",
        source="itunes",
        title=cand.title or meta.title,
        show=cand.show or meta.show,
        duration_s=cand.duration_s or meta.duration_s,
        artwork_url=cand.artwork_url or meta.cover_url,
        feed_url=cand.feed_url or meta.feed_url,
        is_fallback=False,
    )


def _match_feed_item(meta, items, settings) -> ResolvedEpisode | None:
    best: tuple[float, object] | None = None
    for item in items:
        score = score_candidate(
            title=meta.title,
            show=meta.show,
            duration_s=meta.duration_s,
            cand_title=item.title,
            cand_show=meta.show,  # a feed is one show; the name is not repeated
            cand_duration_s=item.duration_s,
            tol_min_s=settings.podcast_duration_tol_min_s,
            tol_pct=settings.podcast_duration_tol_pct,
        )
        if best is None or score > best[0]:
            best = (score, item)
    if best is None or best[0] < settings.podcast_min_score:
        return None
    item = best[1]
    return ResolvedEpisode(
        audio_url=item.audio_url or "",
        source="rss",
        title=item.title or meta.title,
        show=meta.show,
        duration_s=item.duration_s or meta.duration_s,
        artwork_url=meta.cover_url,
        feed_url=meta.feed_url,
        is_fallback=False,
    )


async def _rss_from_feed(db, meta, feed_url: str, settings, proxy: str | None) -> ResolvedEpisode | None:
    items = await list_enclosures(
        feed_url,
        proxy=proxy,
        max_bytes=settings.podcast_max_feed_bytes,
        max_items=settings.podcast_max_feed_items,
    )
    if not items:
        return None
    resolved = _match_feed_item(meta, items, settings)
    if resolved is None or not resolved.audio_url:
        return None
    return resolved



async def find_fallback_match(
    runner,
    settings,
    meta,
    *,
    sources: list[str] | None = None,
    breaker=None,
    cookiejar_dir=None,
    exclude: set[str] | None = None,
) -> tuple[Candidate | None, list[str]]:
    """Search the allowed sources for a copy of the episode (last resort).

    Uses the **podcast** scoring (percentage duration tolerance) rather than the
    music matcher's, and is held to the stricter exclusive threshold by the
    caller.
    """
    if not meta.is_usable():
        return None, []

    skip = exclude or set()
    order = sources if sources is not None else music_matcher.DEFAULT_SOURCES
    query = music_handler_query(meta)

    tried: list[str] = []
    best: Candidate | None = None
    for source in order:
        if source not in music_matcher.SEARCH_URLS:
            continue
        if breaker is not None and breaker.is_open(source):
            continue
        tried.append(source)
        try:
            candidates = await music_matcher.search_source(
                runner,
                source,
                query,
                proxy=music_matcher.source_proxy(settings, source),
                cookiejar_dir=cookiejar_dir,
            )
        except Exception as exc:  # noqa: BLE001
            log.info("podcast fallback: %s search failed: %s", source, type(exc).__name__)
            if breaker is not None:
                breaker.record_failure(source)
            continue
        if breaker is not None:
            breaker.record_success(source)

        for candidate in candidates:
            if candidate.url in skip:
                continue
            candidate.score = score_candidate(
                title=meta.title,
                show=meta.show,
                duration_s=meta.duration_s,
                cand_title=candidate.title,
                cand_show=candidate.uploader,
                cand_duration_s=candidate.duration,
                tol_min_s=settings.podcast_duration_tol_min_s,
                tol_pct=settings.podcast_duration_tol_pct,
            )
            if best is None or candidate.score > best.score:
                best = candidate

        if best is not None and best.score >= settings.podcast_exclusive_min_score:
            break

    if best is None or best.score < settings.podcast_exclusive_min_score:
        return None, tried
    return best, tried


def music_handler_query(meta) -> str:
    show = meta.show or ""
    title = meta.title or ""
    query = f"{show} {title}".strip() or title
    return " ".join(query.split())[:200]


async def resolve_episode_audio(
    db,
    runner,
    settings,
    meta,
    ref,
    *,
    proxy: str | None = None,
    breaker=None,
    cookiejar_dir=None,
) -> ResolvedEpisode | None:
    """Resolve the best downloadable audio for ``meta``, or None."""
    cached = await db.podcast_audio_get(ref.id, RESOLVED_TTL_S)
    if cached:
        resolved = ResolvedEpisode.from_json(cached)
        if resolved is not None:
            return resolved

    # 1. iTunes Search — the publisher's own enclosure.
    candidates: list[ItunesEpisode] = []
    with contextlib.suppress(Exception):
        candidates = await search_episode(db, meta, proxy=proxy, settings=settings)
    for cand in candidates:
        if cand.score >= settings.podcast_min_score and cand.audio_url:
            resolved = _from_itunes(cand, meta)
            with contextlib.suppress(Exception):
                await db.podcast_audio_put(ref.id, resolved.to_json())
            return resolved

    # 2. RSS enclosures — from the best candidate's feed, or the show's feed.
    feed_url = next((c.feed_url for c in candidates if c.feed_url), None) or meta.feed_url
    if feed_url:
        resolved = None
        with contextlib.suppress(Exception):
            resolved = await _rss_from_feed(db, meta, feed_url, settings, proxy)
        if resolved is not None:
            with contextlib.suppress(Exception):
                await db.podcast_audio_put(ref.id, resolved.to_json())
            return resolved

    # 3. Matched copy on an allowed source (Spotify-exclusive case).
    best, tried = await find_fallback_match(
        runner, settings, meta, breaker=breaker, cookiejar_dir=cookiejar_dir
    )
    if best is None:
        log.info("podcast no audio id=%s tried=%s", ref.id, tried)
        return None
    resolved = ResolvedEpisode(
        audio_url=best.url,
        source=best.source,
        title=meta.title or best.title,
        show=meta.show or best.uploader,
        duration_s=meta.duration_s or best.duration,
        artwork_url=meta.cover_url,
        feed_url=meta.feed_url,
        is_fallback=True,
    )
    with contextlib.suppress(Exception):
        await db.podcast_audio_put(ref.id, resolved.to_json())
    return resolved
