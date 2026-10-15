"""Spotify metadata matching (metadata-only, never downloads Spotify audio).

Spotify streams are DRM-protected, so nothing here contacts Spotify's media
pipelines. The package reads public track metadata (title/artist/cover) to find
the *same recording* on a source we are allowed to download (YouTube/SoundCloud)
and then downloads that copy through the normal yt-dlp path.
"""

from __future__ import annotations
