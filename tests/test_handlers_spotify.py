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
    rematch = msg.markups[-1].inline_keyboard[-1][0]
    assert rematch.callback_data == f"sp:rematch:{TRACK_ID}"


# -- rematch ----------------------------------------------------------------


def _prime_state(monkeypatch, settings, match_results):
    """Wire fake metadata + a match() that returns results in order."""
    _enable(monkeypatch, settings)
    seq = list(match_results)

    async def fake_meta(*args, **kwargs):
        return META

    async def fake_match(*args, **kwargs):
        return seq.pop(0)

    monkeypatch.setattr(spotify.metadata, "fetch_metadata", fake_meta)
    monkeypatch.setattr(spotify.matcher, "find_match", fake_match)


def _cand(url: str) -> matcher.Candidate:
    return matcher.Candidate(url, "Never Gonna Give You Up", "Rick Astley", 213.0, "youtube", 0.99)


class _BotStatusMessage:
    """Mimics a message the *bot* sent: no from_user (unlike the user's message)."""

    def __init__(self) -> None:
        self.from_user = None
        self.edits: list[str] = []
        self.markups: list[object] = []

    async def edit_text(self, body, reply_markup=None, **kwargs):
        self.edits.append(body)
        self.markups.append(reply_markup)


async def test_present_keys_flow_by_user_not_status_message(
    services, make_message, monkeypatch, settings
):
    """Regression: the status message is the bot's, so its from_user must not be
    used to key the flow (that would file it under the bot and break the buttons)."""
    from tests.conftest import USER_ID

    spotify._matches.clear()
    _prime_state(monkeypatch, settings, [matcher.MatchResult(_cand("https://youtu.be/good"), ["youtube"], 0.99)])

    status = _BotStatusMessage()
    msg = make_message(text=TRACK_URL)

    async def fake_answer(body, reply_markup=None, **kwargs):
        return status

    monkeypatch.setattr(msg, "answer", fake_answer, raising=False)
    await spotify.on_spotify_message(msg, services.bot)

    assert links._pending.get(USER_ID) is not None
    assert links._pending[USER_ID].url == "https://youtu.be/good"
    assert any("Never Gonna Give You Up" in body for body in status.edits)


async def test_rematch_presents_another_candidate(services, make_message, monkeypatch, settings, make_callback):
    from tests.conftest import USER_ID

    spotify._matches.clear()
    first = matcher.MatchResult(_cand("https://youtu.be/good"), ["youtube"], 0.99)
    _prime_state(monkeypatch, settings, [first])

    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert links._pending[USER_ID].url == "https://youtu.be/good"

    # Second find_match call (rematch) returns a different candidate.
    async def fake_match_alt(*args, **kwargs):
        return matcher.MatchResult(_cand("https://youtu.be/alt"), ["youtube"], 0.98)

    monkeypatch.setattr(spotify.matcher, "find_match", fake_match_alt)

    cb = make_callback(data=f"sp:rematch:{TRACK_ID}", message=msg)
    await spotify.on_rematch(cb)

    assert links._pending[USER_ID].url == "https://youtu.be/alt"
    assert spotify._matches[USER_ID].excluded == {"https://youtu.be/good", "https://youtu.be/alt"}


async def test_rematch_no_more_matches(services, make_message, monkeypatch, settings, make_callback):
    from tests.conftest import USER_ID

    spotify._matches.clear()
    _prime_state(monkeypatch, settings, [matcher.MatchResult(_cand("https://youtu.be/good"), ["youtube"], 0.99)])

    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)

    async def fake_none(*args, **kwargs):
        return matcher.MatchResult(None, ["youtube", "soundcloud"], 0.2)

    monkeypatch.setattr(spotify.matcher, "find_match", fake_none)
    cb = make_callback(data=f"sp:rematch:{TRACK_ID}", message=msg)
    await spotify.on_rematch(cb)

    assert get_text("en", "spotify_no_more_matches") in msg.edits
    assert links._pending[USER_ID].url == "https://youtu.be/good"  # unchanged


async def test_rematch_without_state_alerts(services, make_message, monkeypatch, settings, make_callback):
    spotify._matches.clear()
    msg = make_message(text=TRACK_URL)
    cb = make_callback(data=f"sp:rematch:{TRACK_ID}", message=msg)
    await spotify.on_rematch(cb)
    assert cb.answers and cb.answers[-1][1] is True  # alert shown
    assert get_text("en", "expired_selection") in cb.answers[-1][0]


async def test_rematch_stale_id_ignored(services, make_message, monkeypatch, settings, make_callback):
    from tests.conftest import USER_ID

    spotify._matches.clear()
    _prime_state(monkeypatch, settings, [matcher.MatchResult(_cand("https://youtu.be/good"), ["youtube"], 0.99)])
    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)

    cb = make_callback(data="sp:rematch:doesnotmatch", message=msg)
    await spotify.on_rematch(cb)
    assert cb.answers and cb.answers[-1][1] is True
    assert links._pending[USER_ID].url == "https://youtu.be/good"


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
