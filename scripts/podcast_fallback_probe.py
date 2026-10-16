#!/usr/bin/env python3
"""Throwaway probe: score a Spotify episode against the fallback matcher.

This runs ONLY step 3 of podcast resolution — the YouTube/SoundCloud match that
covers a Spotify-exclusive episode — with the publisher sources (iTunes and the
RSS feed) deliberately skipped, so we can see what the matcher would pick and
how confident it is. It prints the score, the source, and the label the bot
would show.

This is a diagnostic, not a production flag: nothing in the bot imports it.

Usage (on the VPS, from the repo checkout):
    env PYTHONPATH=$PWD ./.venv/bin/python scripts/podcast_fallback_probe.py
    ... --id <spotify-episode-id> [--proxy socks5://127.0.0.1:1080] [--no-fetch]

The default id is the Sekke episode used during development. The search uses the
same gating as the bot, so a home proxy may be required for YouTube.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from bot.config import Settings
from bot.db import Database
from bot.services.downloader import YtDlpRunner
from bot.services.podcast import resolver
from bot.services.podcast.meta import EpisodeMeta, fetch_metadata
from bot.services.spotify.urls import SpotifyRef

DEFAULT_ID = "5o3CeI4Dz5eNzYWDwm08pG"
DEFAULT_TITLE = "قسمت ۸۱: انسان و بتن"
DEFAULT_SHOW = "Sekke Podcast | پادکست سکه"
DEFAULT_DURATION_S = 4804.47


async def _fetch_meta(settings: Settings, ref: SpotifyRef) -> EpisodeMeta | None:
    # A throwaway DB so the probe never touches the production metadata cache.
    with tempfile.TemporaryDirectory(prefix="podcast-probe-") as tmp:
        db = Database(str(Path(tmp) / "probe.db"))
        await db.connect()
        try:
            return await fetch_metadata(db, ref, proxy=settings.proxy_for_podcast())
        finally:
            await db.close()


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.proxy:
        settings.proxy_url = args.proxy
        settings.proxy_url_soundcloud = args.proxy

    ref = SpotifyRef(
        kind="episode", id=args.id, canonical=f"https://open.spotify.com/episode/{args.id}"
    )

    meta: EpisodeMeta | None = None
    if not args.no_fetch:
        try:
            meta = await _fetch_meta(settings, ref)
        except Exception as exc:  # noqa: BLE001 - probe should never crash
            print(f"metadata fetch failed: {type(exc).__name__}: {exc}")
        if meta is None:
            print("metadata fetch returned nothing; using built-in values")

    if meta is None:
        meta = EpisodeMeta(
            spotify_id=args.id,
            title=args.title or DEFAULT_TITLE,
            show=args.show or DEFAULT_SHOW,
            duration_s=args.duration or DEFAULT_DURATION_S,
        )

    print(f"episode  : {args.id}")
    print(f"title    : {meta.title!r}")
    print(f"show     : {meta.show!r}")
    print(f"duration : {meta.duration_s!r} s")
    print(f"query    : {resolver.music_handler_query(meta)!r}")
    print("-" * 60)

    runner = YtDlpRunner(settings)
    best, tried = await resolver.find_fallback_match(runner, settings, meta)

    print(f"sources tried : {tried or '(none)'}")
    if best is None:
        print("score         : -")
        print("source        : -")
        print("label         : not available outside Spotify")
        return 1
    print(
        f"score         : {best.score:.4f}  "
        f"(threshold {settings.podcast_exclusive_min_score})"
    )
    print(f"source        : {best.source}")
    print(f"url           : {best.url}")
    print(f"label         : matched copy on {best.source} (fallback)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", default=DEFAULT_ID, help="Spotify episode id")
    parser.add_argument("--title", default=None, help="override the episode title")
    parser.add_argument("--show", default=None, help="override the show name")
    parser.add_argument("--duration", type=float, default=None, help="override duration (seconds)")
    parser.add_argument("--proxy", default=None, help="override the proxy used by the search sources")
    parser.add_argument(
        "--no-fetch", action="store_true", help="skip Spotify and use the built-in metadata"
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
