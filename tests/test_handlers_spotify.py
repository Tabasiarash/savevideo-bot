from __future__ import annotations

from bot.db import url_hash
from bot.handlers import links, spotify
from bot.i18n import get_text
from bot.services.spotify import matcher
from bot.services.spotify.metadata import TrackMeta
from bot.services.spotify.urls import SpotifyRef

TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"
TRACK_URL = f"https://open.spotify.com/track/{TRACK_ID}"
ALBUM_URL = f"https://open.spotify.com/album/{TRACK_ID}"

META = TrackMeta(
    spotify_id=TRACK_ID,
    kind="track",
    title="Never Gonna Give You Up",
    artists=["Rick Astley"],
    duration_s=213.0,
    cover_url="https://i.scdn.co/image/cover.jpg",
)


def _enable(monkeypatch, settings, **overrides):
    monkeypatch.setattr(settings, "enable_spotify", True, raising=False)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value, raising=False)


# -- filter -----------------------------------------------------------------


def test_first_spotify_url():
    assert spotify.first_spotify_url(f"listen {TRACK_URL} now") == TRACK_URL
    assert spotify.first_spotify_url("https://youtu.be/dQw4w9WgXcQ") is None
    assert spotify.first_spotify_url(None) is None
    assert spotify.first_spotify_url("no links here") is None


async def test_filter_only_matches_spotify(make_message):
    flt = spotify.SpotifyLinkFilter()
    assert await flt(make_message(text=TRACK_URL)) is True
    assert await flt(make_message(text="https://youtu.be/x")) is False


# -- feature gate -----------------------------------------------------------


async def test_disabled_feature_replies_and_stops(services, make_message, monkeypatch, settings):
    monkeypatch.setattr(settings, "enable_spotify", False, raising=False)
    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "spotify_disabled") in msg.sent
    assert links._pending == {}


# -- track flow -------------------------------------------------------------


async def test_bad_link(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)
    msg = make_message(text="https://open.spotify.com/track/short")
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "spotify_bad_link") in msg.edits


async def test_no_metadata(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)

    async def fake_meta(*args, **kwargs):
        return None

    monkeypatch.setattr(spotify.metadata, "fetch_metadata", fake_meta)
    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "spotify_no_metadata") in msg.edits


async def test_no_match(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)

    async def fake_meta(*args, **kwargs):
        return META

    async def fake_match(*args, **kwargs):
        return matcher.MatchResult(None, ["youtube"], 0.1)

    monkeypatch.setattr(spotify.metadata, "fetch_metadata", fake_meta)
    monkeypatch.setattr(spotify.matcher, "find_match", fake_match)
    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "spotify_no_match") in msg.edits


async def test_track_match_presents_picker(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)

    async def fake_meta(*args, **kwargs):
        return META

    async def fake_match(*args, **kwargs):
        cand = matcher.Candidate("https://youtu.be/good", "Never Gonna Give You Up", "Rick Astley", 213.0, "youtube", 0.99)
        return matcher.MatchResult(cand, ["youtube"], 0.99)

    monkeypatch.setattr(spotify.metadata, "fetch_metadata", fake_meta)
    monkeypatch.setattr(spotify.matcher, "find_match", fake_match)

    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)

    from tests.conftest import USER_ID

    flow = links._pending.get(USER_ID)
    assert flow is not None
    assert flow.url == "https://youtu.be/good"
    assert flow.cache_key == url_hash(f"spotify:{TRACK_ID}")
    assert flow.audio_meta is not None and flow.audio_meta.title == "Never Gonna Give You Up"
    assert flow.postprocess is not None
    assert any("Never Gonna Give You Up" in body for body in msg.edits)
    assert msg.markups[-1] is not None, "picker keyboard not attached"


# -- collections ------------------------------------------------------------


async def test_collection_disabled(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings, spotify_enable_collections=False)
    msg = make_message(text=ALBUM_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "spotify_collections_disabled") in msg.edits


async def test_collection_needs_api(services, make_message, monkeypatch, settings):
    _enable(
        monkeypatch,
        settings,
        spotify_enable_collections=True,
        spotify_client_id="",
        spotify_client_secret="",
    )
    msg = make_message(text=ALBUM_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "spotify_collection_needs_api") in msg.edits


async def test_collection_expands_and_downloads(services, make_message, monkeypatch, settings):
    _enable(
        monkeypatch,
        settings,
        spotify_enable_collections=True,
        spotify_client_id="cid",
        spotify_client_secret="sec",
        spotify_max_items=3,
    )

    refs = [SpotifyRef("track", f"id{n}", f"https://open.spotify.com/track/id{n}") for n in range(2)]

    async def fake_list(*args, **kwargs):
        return refs

    async def fake_meta(*args, **kwargs):
        return META

    async def fake_match(*args, **kwargs):
        cand = matcher.Candidate("https://youtu.be/good", "Never Gonna Give You Up", "Rick Astley", 213.0, "youtube", 0.99)
        return matcher.MatchResult(cand, ["youtube"], 0.99)

    calls: list[str] = []

    async def fake_download_flow(bot, flow, fmt, user_id):
        calls.append(fmt)
        return "ok"

    monkeypatch.setattr(spotify.metadata, "list_collection_tracks", fake_list)
    monkeypatch.setattr(spotify.metadata, "fetch_metadata", fake_meta)
    monkeypatch.setattr(spotify.matcher, "find_match", fake_match)
    monkeypatch.setattr(links, "download_flow", fake_download_flow)

    msg = make_message(text=ALBUM_URL)
    await spotify.on_spotify_message(msg, services.bot)

    assert calls == ["audio_mp3", "audio_mp3"]
    assert get_text("en", "spotify_collection_done", ok=2, fail=0) in msg.sent
