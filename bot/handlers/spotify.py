"""Spotify links: metadata match instead of (DRM-protected) download.

A Spotify link never reaches the generic link handler. This router is registered
*before* ``links.router`` and only matches messages that contain a Spotify URL,
so every other link still falls through. It is always registered, even when
``ENABLE_SPOTIFY`` is off, so a Spotify link gets a clear "feature disabled /
DRM" message rather than an obscure yt-dlp error.

When enabled, the flow is:

1. read public metadata (title/artist/cover) for the Spotify item;
2. search allowed sources (YouTube/SoundCloud) for the same recording;
3. present the normal audio picker for the matched URL, with the Spotify cover
   attached and the downloaded file tagged via ffmpeg.

Collections (album/playlist/show/artist) are expanded only when
``SPOTIFY_ENABLE_COLLECTIONS`` is set and API credentials are configured; each
matched track is then downloaded and sent in turn.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from bot.config import settings
from bot.db import url_hash
from bot.deps import get_services
from bot.handlers import links
from bot.handlers.common import is_banned, register_user, text
from bot.services import podcast, tagger
from bot.services.breaker import Breaker
from bot.services.downloader import cleanup_dir
from bot.services.spotify import matcher, metadata, urls
from bot.utils import extract_urls

log = logging.getLogger(__name__)

router = Router(name="spotify")

# One breaker per search source, shared across requests for this process.
_breaker = Breaker(threshold=3, cooldown=300.0)

# Containers we can write tags into.
_AUDIO_SUFFIXES = tagger.SUPPORTED_SUFFIXES

# Callback data for the "try another match" button.
_REMATCH_PREFIX = "sp:rematch"

# How many candidates a single card may cycle through before the "try another
# match" button is dropped, so a request cannot hammer the sources forever.
MAX_REMATCH_CANDIDATES = 3


@dataclass
class _MatchState:
    """Enough context to re-run matching for an already-presented track."""

    user_id: int
    ref: urls.SpotifyRef
    meta: metadata.TrackMeta
    tmpdir: Path
    lang: str
    status_message: Message
    excluded: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > links.PENDING_TTL_S


# At most one active Spotify match per user (rate limiting prevents overlap).
_matches: dict[int, _MatchState] = {}


def first_spotify_url(value: str | None) -> str | None:
    if not value:
        return None
    for candidate in extract_urls(value):
        if urls.is_spotify_url(candidate):
            return candidate
    return None


class SpotifyLinkFilter(BaseFilter):
    """Matches only messages that carry a Spotify URL."""

    async def __call__(self, message: Message) -> bool:
        return first_spotify_url(message.text) is not None


@router.message(F.text, SpotifyLinkFilter())
async def on_spotify_message(message: Message, bot: Bot) -> None:
    srv = get_services()
    user = message.from_user
    if user is None:
        return
    lang = await register_user(srv.db, message)

    if await is_banned(srv.db, user.id):
        await message.answer(text(lang, "banned"))
        return

    raw = first_spotify_url(message.text)
    if raw is None:
        return

    # Podcasts are an independent feature: an episode link is handled even when
    # ENABLE_SPOTIFY is off. Route on the link kind as early as possible.
    probe = urls.parse_spotify_url(raw)
    if probe is not None and probe.kind == "episode":
        await _podcast_entry(message, bot, probe, lang)
        return
    if probe is not None and probe.kind == "show" and settings.enable_podcasts:
        await message.answer(text(lang, "podcast_show_link"))
        return
    if probe is None and urls.is_short_link(raw) and settings.enable_podcasts:
        resolved = await _resolve(raw)
        if resolved is not None and resolved.kind == "episode":
            await _podcast_entry(message, bot, resolved, lang)
            return
        if resolved is not None and resolved.kind == "show":
            await message.answer(text(lang, "podcast_show_link"))
            return

    # Always answer Spotify links, even with the feature off.
    if not settings.enable_spotify:
        await message.answer(text(lang, "spotify_disabled"))
        return

    if await srv.ratelimiter.has_active_job(user.id):
        await message.answer(text(lang, "active_job"))
        return

    status = await message.answer(text(lang, "spotify_reading"))
    ref = await _resolve(raw)
    if ref is None:
        await links.edit_message(message=status, body=text(lang, "spotify_bad_link"))
        return

    if ref.is_collection:
        await _handle_collection(message, bot, ref, lang, status)
        return

    await _handle_track(message, ref, lang, status)


async def _resolve(raw: str):
    """Parse a direct link, or resolve a short link to a direct one."""
    ref = urls.parse_spotify_url(raw)
    if ref is not None:
        return ref
    if urls.is_short_link(raw):
        try:
            return await urls.resolve_short_link(raw, proxy=settings.proxy_for_spotify())
        except Exception:  # noqa: BLE001
            log.info("spotify short-link resolution failed", exc_info=True)
            return None
    return None


async def _fetch_meta(ref) -> metadata.TrackMeta | None:
    srv = get_services()
    try:
        return await metadata.fetch_metadata(
            srv.db,
            ref,
            proxy=settings.proxy_for_spotify(),
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
        )
    except Exception:  # noqa: BLE001
        log.info("spotify metadata fetch failed", exc_info=True)
        return None


async def _match(meta: metadata.TrackMeta, tmpdir: Path, *, exclude: set[str] | None = None):
    srv = get_services()
    return await matcher.find_match(
        srv.runner,
        settings,
        meta,
        sources=settings.spotify_sources(),
        breaker=_breaker,
        cookiejar_dir=tmpdir,
        exclude=exclude,
    )


async def _handle_track(message: Message, ref, lang: str, status: Message) -> None:
    user = message.from_user
    assert user is not None

    meta = await _fetch_meta(ref)
    if meta is None or not meta.is_usable():
        await links.edit_message(message=status, body=text(lang, "spotify_no_metadata"))
        return

    await links.edit_message(message=status, body=text(lang, "spotify_searching"))

    tmpdir = links.make_tmpdir(user.id)
    result = await _match(meta, tmpdir)
    candidate = result.candidate
    if candidate is None:
        log.info("spotify no match id=%s tried=%s best=%s", ref.id, result.tried, result.best_score)
        await links.edit_message(message=status, body=text(lang, "spotify_no_match"))
        cleanup_dir(tmpdir)
        return

    state = _MatchState(
        user_id=user.id, ref=ref, meta=meta, tmpdir=tmpdir, lang=lang, status_message=status
    )
    _matches[user.id] = state
    await _present(state, candidate)


# --------------------------------------------------------------------------
# Podcast episodes (publisher feed, never Spotify audio)
# --------------------------------------------------------------------------

async def _podcast_entry(message: Message, bot: Bot, ref, lang: str) -> None:
    """Enter the podcast lane for an episode link (gated by ENABLE_PODCASTS)."""
    srv = get_services()
    user = message.from_user
    if user is None:
        return

    if not settings.enable_podcasts:
        await message.answer(text(lang, "podcast_disabled"))
        return

    if await srv.ratelimiter.has_active_job(user.id):
        await message.answer(text(lang, "active_job"))
        return

    status = await message.answer(text(lang, "podcast_reading"))
    await _handle_episode(message, bot, ref, lang, status)


async def _handle_episode(message: Message, bot: Bot, ref, lang: str, status: Message) -> None:
    srv = get_services()
    user = message.from_user
    assert user is not None

    # Note: the episode title and show name are sent to Apple's iTunes Search API.
    meta = await podcast.fetch_episode_meta(srv.db, ref, proxy=settings.proxy_for_podcast())
    if meta is None or not meta.is_usable():
        await links.edit_message(message=status, body=text(lang, "podcast_no_metadata"))
        return

    if meta.duration_s and meta.duration_s > settings.podcast_max_duration_min * 60:
        await links.edit_message(
            message=status, body=text(lang, "podcast_too_long", max=settings.podcast_max_duration_min)
        )
        return

    await links.edit_message(message=status, body=text(lang, "podcast_searching"))

    tmpdir = links.make_tmpdir(user.id)
    resolved = await podcast.resolve_episode_audio(
        srv.db,
        srv.runner,
        settings,
        meta,
        ref,
        proxy=settings.proxy_for_podcast(),
        breaker=_breaker,
        cookiejar_dir=tmpdir,
    )
    if resolved is None:
        log.info("podcast no audio id=%s", ref.id)
        await links.edit_message(message=status, body=text(lang, "podcast_no_match"))
        cleanup_dir(tmpdir)
        return

    if resolved.is_fallback:
        await _present_episode_fallback(message, ref, meta, resolved, tmpdir, lang, status)
        return

    await _deliver_episode_enclosure(message, bot, ref, meta, resolved, tmpdir, lang, status)


def _episode_audio_meta(meta, resolved, cover_path: Path) -> links.AudioMeta:
    return links.AudioMeta(
        title=resolved.title or meta.title,
        performer=resolved.show or meta.show,
        cover=cover_path,
    )


def _make_episode_postprocess(meta, resolved, cover_path: Path):
    async def _postprocess(parts: list[Path]) -> None:
        cover = await _download_artwork(resolved.artwork_url or meta.cover_url, cover_path)
        for path in parts:
            if path.suffix.lower() not in _AUDIO_SUFFIXES:
                continue
            ok = await tagger.tag_audio(
                path,
                title=resolved.title or meta.title,
                artist=resolved.show or meta.show,
                album=resolved.show or meta.show,
                date=meta.released,
                cover=cover,
            )
            if not ok:
                log.info("episode tagging skipped/failed for %s", path.name)

    return _postprocess


def _cover_allowed(url: str) -> bool:
    from urllib.parse import urlparse

    if urls.is_spotify_cdn(url):
        return True
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("mzstatic.com") or host.endswith(".scdn.co")


async def _download_artwork(url: str | None, dest: Path) -> Path | None:
    if not url:
        return None
    from bot.services.spotify import http as spotify_http

    try:
        resp = await spotify_http.fetch(
            url,
            proxy=settings.proxy_for_podcast(),
            allow_redirects=True,
            is_allowed=_cover_allowed,
            max_bytes=5_000_000,
        )
    except Exception:  # noqa: BLE001
        return None
    if resp.status == 200 and resp.content:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return dest
        except OSError:
            return None
    return None


async def _present_episode_fallback(
    message: Message, ref, meta, resolved, tmpdir: Path, lang: str, status: Message
) -> None:
    """Offer a matched (non-publisher) copy, clearly labelled as such."""
    user = message.from_user
    assert user is not None

    cover_path = tmpdir / "cover.jpg"
    info = {
        "id": ref.id,
        "title": resolved.title or meta.title,
        "uploader": resolved.show or meta.show or resolved.source,
        "duration": resolved.duration_s or meta.duration_s or 0.0,
    }
    flow = links.PendingFlow(
        url=resolved.audio_url,
        url_site=resolved.source,
        info=info,
        tmpdir=tmpdir,
        title=resolved.title or meta.title or "episode",
        duration=float(resolved.duration_s or meta.duration_s or 0.0),
        uploader=resolved.show or meta.show or "",
        prefix="dl",
        playlist_items=None,
        lang=lang,
        status_message=status,
        cache_key=url_hash(f"podcast:{ref.id}"),
        audio_meta=_episode_audio_meta(meta, resolved, cover_path),
        postprocess=_make_episode_postprocess(meta, resolved, cover_path),
    )
    links.remember_flow(user.id, flow)
    body = text(
        lang,
        "podcast_fallback_pick",
        title=resolved.title or meta.title or "?",
        show=resolved.show or meta.show or "?",
        source=resolved.source,
    )
    await links.edit_message(message=status, body=body, reply_markup=links.picker_keyboard(flow))


async def _deliver_episode_enclosure(
    message: Message, bot: Bot, ref, meta, resolved, tmpdir: Path, lang: str, status: Message
) -> None:
    """Download the publisher's enclosure and deliver it (never transcoded)."""
    user = message.from_user
    assert user is not None

    await links.edit_message(message=status, body=text(lang, "podcast_downloading"))
    try:
        path = await podcast.stream_enclosure(
            resolved.audio_url,
            tmpdir,
            max_bytes=settings.podcast_max_enclosure_bytes,
            proxy=settings.proxy_for_podcast(),
            filename_hint=resolved.title or meta.title or "episode",
        )
    except podcast.EnclosureTooLarge:
        await links.edit_message(
            message=status, body=text(lang, "podcast_too_large", max=settings.podcast_max_enclosure_mb)
        )
        cleanup_dir(tmpdir)
        return
    except podcast.EnclosureError:
        log.info("podcast enclosure failed id=%s", ref.id, exc_info=True)
        await links.edit_message(message=status, body=text(lang, "podcast_download_failed"))
        cleanup_dir(tmpdir)
        return

    cover_path = tmpdir / "cover.jpg"
    info = {
        "id": ref.id,
        "title": resolved.title or meta.title,
        "uploader": resolved.show or meta.show,
        "duration": resolved.duration_s or meta.duration_s or 0.0,
    }
    flow = links.PendingFlow(
        url=resolved.audio_url,
        url_site="podcast",
        info=info,
        tmpdir=tmpdir,
        title=resolved.title or meta.title or "episode",
        duration=float(resolved.duration_s or meta.duration_s or 0.0),
        uploader=resolved.show or meta.show or "",
        prefix="dl",
        playlist_items=None,
        lang=lang,
        status_message=status,
        cache_key=url_hash(f"podcast:{ref.id}"),
        audio_meta=_episode_audio_meta(meta, resolved, cover_path),
        postprocess=_make_episode_postprocess(meta, resolved, cover_path),
    )
    outcome = await links.deliver_local_file(bot, flow, path, user.id)
    if outcome == "active_job":
        await links.edit_message(message=status, body=text(lang, "active_job"))


@router.callback_query(F.data.startswith(_REMATCH_PREFIX))
async def on_rematch(callback: CallbackQuery) -> None:
    """Re-run the source search, skipping candidates already offered."""
    user = callback.from_user
    state = _matches.get(user.id)
    if state is None or callback.data != f"{_REMATCH_PREFIX}:{state.ref.id}":
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        return

    if state.is_expired():
        _matches.pop(user.id, None)
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        await links.edit_message(
            message=state.status_message, body=text(state.lang, "expired_selection")
        )
        return

    if len(state.excluded) >= MAX_REMATCH_CANDIDATES:
        await callback.answer(text(state.lang, "spotify_no_more_matches"), show_alert=True)
        return

    await callback.answer()
    await links.edit_message(message=state.status_message, body=text(state.lang, "spotify_searching"))

    result = await _match(state.meta, state.tmpdir, exclude=state.excluded)
    candidate = result.candidate
    if candidate is None:
        log.info("spotify no more matches id=%s tried=%s", state.ref.id, result.tried)
        await links.edit_message(message=state.status_message, body=text(state.lang, "spotify_no_more_matches"))
        return

    await _present(state, candidate)


def _audio_meta(meta: metadata.TrackMeta, cover_path: Path) -> links.AudioMeta:
    return links.AudioMeta(
        title=meta.title,
        performer=meta.artist_line() or None,
        cover=cover_path,
    )


def _make_postprocess(meta: metadata.TrackMeta, cover_path: Path):
    async def _postprocess(parts: list[Path]) -> None:
        cover = await _download_cover(meta.cover_url, cover_path)
        for path in parts:
            if path.suffix.lower() in _AUDIO_SUFFIXES:
                ok = await tagger.tag_audio(
                    path,
                    title=meta.title,
                    artist=meta.artist_line() or None,
                    cover=cover,
                )
                if not ok:
                    log.info("tagging skipped/failed for %s", path.name)

    return _postprocess


async def _download_cover(url: str | None, dest: Path) -> Path | None:
    if not url:
        return None
    from bot.services.spotify import http as spotify_http

    try:
        resp = await spotify_http.fetch(
            url,
            proxy=settings.proxy_for_spotify(),
            allow_redirects=True,
            is_allowed=urls.is_spotify_cdn,
            max_bytes=5_000_000,
        )
    except Exception:  # noqa: BLE001
        return None
    if resp.status == 200 and resp.content:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return dest
        except OSError:
            return None
    return None


async def _present(state: _MatchState, candidate: matcher.Candidate) -> None:
    user_id = state.user_id

    # Track shown candidates so rematch can skip them.
    state.excluded.add(candidate.url)

    ref = state.ref
    meta = state.meta
    tmpdir = state.tmpdir
    lang = state.lang

    info = {
        "id": ref.id,
        "title": meta.title or candidate.title,
        "uploader": meta.artist_line() or candidate.uploader,
        "duration": meta.duration_s or candidate.duration or 0.0,
    }
    cover_path = tmpdir / "cover.jpg"
    flow = links.PendingFlow(
        url=candidate.url,
        url_site=candidate.source,
        info=info,
        tmpdir=tmpdir,
        title=meta.title or candidate.title,
        duration=float(meta.duration_s or candidate.duration or 0.0),
        uploader=meta.artist_line() or candidate.uploader,
        prefix="dl",
        playlist_items=None,
        lang=lang,
        status_message=state.status_message,
        cache_key=url_hash(f"spotify:{ref.id}"),
        audio_meta=_audio_meta(meta, cover_path),
        postprocess=_make_postprocess(meta, cover_path),
    )
    links.remember_flow(user_id, flow)

    body = text(
        lang,
        "spotify_pick",
        title=meta.title or candidate.title,
        artist=meta.artist_line() or candidate.uploader or "?",
        source=candidate.source,
    )

    kb = links.picker_keyboard(flow)
    if len(state.excluded) < MAX_REMATCH_CANDIDATES:
        kb.inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=text(lang, "button_try_another"),
                    callback_data=f"{_REMATCH_PREFIX}:{ref.id}",
                )
            ]
        )

    await links.edit_message(message=state.status_message, body=body, reply_markup=kb)

async def _handle_collection(message: Message, bot: Bot, ref, lang: str, status: Message) -> None:
    srv = get_services()
    user = message.from_user
    assert user is not None

    if not settings.spotify_enable_collections:
        await links.edit_message(message=status, body=text(lang, "spotify_collections_disabled"))
        return
    if not (settings.spotify_client_id and settings.spotify_client_secret):
        await links.edit_message(message=status, body=text(lang, "spotify_collection_needs_api"))
        return

    try:
        refs = await metadata.list_collection_tracks(
            srv.db,
            ref,
            proxy=settings.proxy_for_spotify(),
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            limit=settings.spotify_max_items,
        )
    except Exception:  # noqa: BLE001
        log.info("spotify collection listing failed", exc_info=True)
        refs = []

    if not refs:
        await links.edit_message(message=status, body=text(lang, "spotify_no_metadata"))
        return

    await links.edit_message(
        message=status,
        body=text(lang, "spotify_collection_found", count=len(refs), max=settings.spotify_max_items),
    )

    ok = 0
    fail = 0
    for track_ref in refs:
        meta = await _fetch_meta(track_ref)
        if meta is None or not meta.is_usable():
            fail += 1
            continue
        tmpdir = links.make_tmpdir(user.id)
        result = await _match(meta, tmpdir)
        candidate = result.candidate
        if candidate is None:
            fail += 1
            cleanup_dir(tmpdir)
            continue

        info = {
            "id": track_ref.id,
            "title": meta.title or candidate.title,
            "uploader": meta.artist_line() or candidate.uploader,
            "duration": meta.duration_s or candidate.duration or 0.0,
        }
        cover_path = tmpdir / "cover.jpg"
        flow = links.PendingFlow(
            url=candidate.url,
            url_site=candidate.source,
            info=info,
            tmpdir=tmpdir,
            title=meta.title or candidate.title,
            duration=float(meta.duration_s or candidate.duration or 0.0),
            uploader=meta.artist_line() or candidate.uploader,
            prefix="dl",
            playlist_items=None,
            lang=lang,
            status_message=None,
            cache_key=url_hash(f"spotify:{track_ref.id}"),
            audio_meta=_audio_meta(meta, cover_path),
            postprocess=_make_postprocess(meta, cover_path),
        )
        outcome = await links.download_flow(bot, flow, "audio_mp3", user.id)
        if outcome == "ok":
            ok += 1
        else:
            fail += 1
            cleanup_dir(tmpdir)

    await message.answer(text(lang, "spotify_collection_done", ok=ok, fail=fail))
