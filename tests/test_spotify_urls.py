from __future__ import annotations

from types import SimpleNamespace

import pytest
from bot.services.spotify import urls

TRACK = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"


def test_recognises_direct_links():
    assert urls.is_spotify_url(TRACK) is True
    assert urls.is_spotify_url("https://example.com/track/x") is False
    assert urls.is_spotify_url("") is False


def test_parse_plain_track():
    ref = urls.parse_spotify_url(TRACK)
    assert ref is not None
    assert (ref.kind, ref.id) == ("track", TRACK_ID)
    assert ref.canonical == TRACK
    assert ref.is_collection is False


def test_parse_tolerates_locale_prefix_and_query():
    ref = urls.parse_spotify_url(f"https://open.spotify.com/intl-de/track/{TRACK_ID}?si=abc123")
    assert ref is not None
    assert (ref.kind, ref.id) == ("track", TRACK_ID)


def test_parse_legacy_host_and_uri():
    assert urls.parse_spotify_url(f"https://play.spotify.com/track/{TRACK_ID}").id == TRACK_ID
    assert urls.parse_spotify_url(f"spotify:track:{TRACK_ID}").id == TRACK_ID


def test_parse_collections():
    album = urls.parse_spotify_url(f"https://open.spotify.com/album/{TRACK_ID}")
    assert album is not None and album.is_collection is True
    playlist = urls.parse_spotify_url(f"spotify:playlist:{TRACK_ID}")
    assert playlist is not None and playlist.is_collection is True


def test_parse_rejects_junk():
    assert urls.parse_spotify_url("https://open.spotify.com/track/short") is None
    assert urls.parse_spotify_url("https://open.spotify.com/unknown/" + TRACK_ID) is None
    assert urls.parse_spotify_url("https://not-spotify.com/track/" + TRACK_ID) is None
    assert urls.parse_spotify_url("") is None


def test_short_links_are_not_direct():
    short = "https://spotify.link/abcd1234"
    assert urls.is_short_link(short) is True
    assert urls.parse_spotify_url(short) is None


async def test_resolve_short_link_follows_to_track(monkeypatch):
    calls: list[str] = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        if url == "https://spotify.link/abcd1234":
            return SimpleNamespace(status=302, headers={"location": f"https://open.spotify.com/track/{TRACK_ID}"}, text="", url=url)
        return SimpleNamespace(status=200, headers={}, text="", url=url)

    monkeypatch.setattr(urls, "fetch", fake_fetch)
    ref = await urls.resolve_short_link("https://spotify.link/abcd1234")
    assert ref is not None and ref.id == TRACK_ID
    assert calls == ["https://spotify.link/abcd1234"]


async def test_resolve_short_link_rejects_foreign_redirect(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return SimpleNamespace(status=302, headers={"location": "https://evil.example/track/x"}, text="", url=url)

    monkeypatch.setattr(urls, "fetch", fake_fetch)
    assert await urls.resolve_short_link("https://spotify.link/abcd1234") is None


async def test_resolve_short_link_gives_up(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return SimpleNamespace(status=200, headers={}, text="", url=url)

    monkeypatch.setattr(urls, "fetch", fake_fetch)
    assert await urls.resolve_short_link("https://spotify.link/abcd1234") is None


@pytest.mark.parametrize("host", ["spotify.link", "spoti.fi", "link.spotify.com"])
def test_all_short_hosts(host):
    assert urls.is_short_link(f"https://{host}/xyz") is True
