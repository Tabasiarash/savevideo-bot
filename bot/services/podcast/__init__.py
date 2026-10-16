"""Resolve a Spotify podcast episode to publisher-hosted audio.

Spotify never serves the audio here: an episode is matched to the publisher's
own enclosure (the RSS file a podcast app would download), located through the
iTunes Search API, then fetched with the bot's own hardened HTTP client and
sent as-is (tagged, not transcoded).

Resolution order: iTunes Search → RSS enclosures → a YouTube/SoundCloud match
as a last resort (labelled, because it may differ from the episode).

Note: the episode title and show name are sent to Apple's iTunes Search API.
"""

from __future__ import annotations

from .download import EnclosureError, EnclosureTooLarge, stream_enclosure
from .feed import FeedItem, list_enclosures
from .itunes import ItunesEpisode, search_episode
from .meta import EpisodeMeta
from .meta import fetch_metadata as fetch_episode_meta
from .resolver import ResolvedEpisode, resolve_episode_audio

__all__ = [
    "EnclosureError",
    "EnclosureTooLarge",
    "EpisodeMeta",
    "FeedItem",
    "ItunesEpisode",
    "ResolvedEpisode",
    "fetch_episode_meta",
    "list_enclosures",
    "resolve_episode_audio",
    "search_episode",
    "stream_enclosure",
]
