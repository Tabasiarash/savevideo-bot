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
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.config import settings
from bot.db import url_hash
from bot.deps import get_services
from bot.handlers import links
from bot.handlers.common import is_banned, register_user, text
from bot.services import tagger
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

    await _handle_track(message, bot, ref, lang, status)


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


async def _match(meta: metadata.TrackMeta, tmpdir: Path):
    srv = get_services()
    return await matcher.find_match(
        srv.runner,
        settings,
        meta,
        sources=settings.spotify_sources(),
        breaker=_breaker,
        cookiejar_dir=tmpdir,
    )


async def _handle_track(message: Message, bot: Bot, ref, lang: str, status: Message) -> None:
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

    await _present(message, bot, ref, meta, candidate, tmpdir, lang, status)


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
            url, proxy=settings.proxy_for_spotify(), allow_redirects=True, max_bytes=5_000_000
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


async def _present(
    message: Message,
    bot: Bot,
    ref,
    meta: metadata.TrackMeta,
    candidate: matcher.Candidate,
    tmpdir: Path,
    lang: str,
    status: Message,
) -> None:
    user = message.from_user
    assert user is not None

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
        status_message=status,
        cache_key=url_hash(f"spotify:{ref.id}"),
        audio_meta=_audio_meta(meta, cover_path),
        postprocess=_make_postprocess(meta, cover_path),
    )
    links.remember_flow(user.id, flow)

    body = text(
        lang,
        "spotify_pick",
        title=meta.title or candidate.title,
        artist=meta.artist_line() or candidate.uploader or "?",
        source=candidate.source,
    )
    await links.edit_message(message=status, body=body, reply_markup=links.picker_keyboard(flow))


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
