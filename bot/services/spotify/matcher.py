"""Find the same recording on a downloadable source ("metadata match").

Spotify audio is DRM-protected and never fetched. Instead we take the metadata
read by :mod:`bot.services.spotify.metadata` and search allowed sources
(YouTube/SoundCloud) for the closest recording, using the existing yt-dlp
runner. Candidates are scored on title, artist and duration; a match is accepted
only when it clears ``SPOTIFY_MIN_SCORE``.

Each source has its own :class:`~bot.services.breaker.Breaker` key so a source
that keeps failing is skipped for a cooldown instead of stalling every request.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.services.breaker import Breaker
from bot.services.spotify.metadata import TrackMeta

log = logging.getLogger(__name__)

# Search URL templates per source. ``q`` is the quoted query.
SEARCH_URLS = {
    "youtube": "ytsearch5:{q}",
    "soundcloud": "scsearch5:{q}",
}
DEFAULT_SOURCES = ("youtube", "soundcloud")

# Components written into the search box but not the Spotify track name.
_NOISE = re.compile(
    r"\b(official|video|audio|lyrics?|lyric|hd|hq|4k|mv|m/v|visualizer|remaster(?:ed)?|"
    r"remix|cover|live|feat|ft|prod)\b",
    re.I,
)
_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_WS = re.compile(r"\s+")

WEIGHT_TITLE = 0.60
WEIGHT_ARTIST = 0.25
WEIGHT_DURATION = 0.15


@dataclass
class Candidate:
    url: str
    title: str
    uploader: str
    duration: float
    source: str
    score: float = 0.0


@dataclass
class MatchResult:
    candidate: Candidate | None
    tried: list[str]
    best_score: float = 0.0


def normalize_text(text: str | None) -> str:
    """Lower-case, drop bracketed asides/noise words and punctuation."""
    value = _BRACKETS.sub(" ", text or "")
    value = _NOISE.sub(" ", value).lower()
    value = _NON_ALNUM.sub(" ", value)
    return _WS.sub(" ", value).strip()


def _seq_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _token_ratio(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def text_similarity(a: str | None, b: str | None) -> float:
    """Similarity in 0..1, tolerant of reordering and extra words."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    return round(max(_seq_ratio(na, nb), _token_ratio(na, nb)), 4)


def _duration_score(expected: float | None, actual: float | None, tolerance: int) -> float | None:
    if not expected or not actual:
        return None
    drift = abs(expected - actual)
    return round(max(0.0, 1.0 - drift / max(1.0, float(tolerance))), 4)


def score_candidate(meta: TrackMeta, candidate: Candidate, tolerance: int) -> float:
    """Weighted 0..1 score; components with missing data are renormalised out."""
    parts: list[tuple[float, float]] = [(text_similarity(meta.title, candidate.title), WEIGHT_TITLE)]
    if meta.artists:
        parts.append((text_similarity(meta.artist_line(), candidate.uploader), WEIGHT_ARTIST))
    duration = _duration_score(meta.duration_s, candidate.duration, tolerance)
    if duration is not None:
        parts.append((duration, WEIGHT_DURATION))

    total_weight = sum(w for _, w in parts) or 1.0
    return round(sum(v * w for v, w in parts) / total_weight, 4)


def candidate_from_entry(entry: dict[str, Any], source: str) -> Candidate | None:
    url = str(entry.get("webpage_url") or entry.get("url") or "")
    if not url.lower().startswith(("http://", "https://")):
        return None
    uploader = entry.get("uploader") or entry.get("channel") or entry.get("artist") or ""
    try:
        duration = float(entry.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return Candidate(
        url=url,
        title=str(entry.get("title") or ""),
        uploader=str(uploader),
        duration=duration,
        source=source,
    )


def build_query(meta: TrackMeta) -> str:
    artist = meta.artist_line()
    title = meta.title or ""
    query = f"{artist} {title}".strip() or title
    return " ".join(query.split())[:200]


def source_proxy(settings, source: str) -> str | None:
    """The proxy a source would use, as a non-empty string or ``None``."""
    if source == "youtube":
        chosen = settings.proxy_url
    elif source == "soundcloud":
        chosen = settings.proxy_url_soundcloud or settings.proxy_url
    else:
        chosen = settings.proxy_url
    chosen = (chosen or "").strip()
    return chosen or None


def _search_url(source: str, query: str) -> str | None:
    template = SEARCH_URLS.get(source)
    if not template:
        return None
    return template.format(q=query)


async def search_source(
    runner,
    source: str,
    query: str,
    *,
    proxy: str | None = None,
    cookiejar_dir: Path | None = None,
) -> list[Candidate]:
    """Run one source's search; returns candidates (may be empty)."""
    url = _search_url(source, query)
    if url is None:
        return []
    info = await runner.extract_info(
        url, playlist=True, flat=True, proxy=proxy, cookiejar_dir=cookiejar_dir
    )
    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        entries = [info] if isinstance(info, dict) else []
    candidates: list[Candidate] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            candidate = candidate_from_entry(entry, source)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


async def find_match(
    runner,
    settings,
    meta: TrackMeta,
    *,
    sources: list[str] | None = None,
    breaker: Breaker | None = None,
    cookiejar_dir: Path | None = None,
) -> MatchResult:
    """Search the configured sources and return the best candidate over the bar."""
    if not meta.is_usable():
        return MatchResult(None, [])

    order = sources if sources is not None else list(DEFAULT_SOURCES)
    tol = settings.spotify_duration_tolerance_s
    min_score = settings.spotify_min_score
    query = build_query(meta)

    tried: list[str] = []
    best: Candidate | None = None
    for source in order:
        if source not in SEARCH_URLS:
            continue
        if breaker is not None and breaker.is_open(source):
            log.info("spotify matcher: skipping %s (breaker open)", source)
            continue

        tried.append(source)
        try:
            candidates = await search_source(
                runner,
                source,
                query,
                proxy=source_proxy(settings, source),
                cookiejar_dir=cookiejar_dir,
            )
        except Exception as exc:  # noqa: BLE001 - a source failing must not abort
            log.info("spotify matcher: %s search failed: %s", source, type(exc).__name__)
            if breaker is not None:
                breaker.record_failure(source)
            continue

        if breaker is not None:
            breaker.record_success(source)

        for candidate in candidates:
            candidate.score = score_candidate(meta, candidate, tol)
            if best is None or candidate.score > best.score:
                best = candidate

        if best is not None and best.score >= min_score:
            break

    if best is None or best.score < min_score:
        return MatchResult(None, tried, best.score if best else 0.0)
    return MatchResult(best, tried, best.score)
