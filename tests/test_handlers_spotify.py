from __future__ import annotations

import time

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


async def test_rematch_after_ttl_expires(
    services, make_message, monkeypatch, settings, make_callback
):
    import time

    from tests.conftest import USER_ID

    spotify._matches.clear()
    _prime_state(monkeypatch, settings, [matcher.MatchResult(_cand("https://youtu.be/good"), ["youtube"], 0.99)])
    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)

    # Age the state past the shared TTL.
    spotify._matches[USER_ID].created_at = time.time() - links.PENDING_TTL_S - 1

    cb = make_callback(data=f"sp:rematch:{TRACK_ID}", message=msg)
    await spotify.on_rematch(cb)

    assert cb.answers and cb.answers[-1][1] is True
    assert get_text("en", "expired_selection") in cb.answers[-1][0]
    assert USER_ID not in spotify._matches


async def test_rematch_button_dropped_after_third_candidate(
    services, make_message, monkeypatch, settings, make_callback
):
    from tests.conftest import USER_ID

    spotify._matches.clear()
    _prime_state(monkeypatch, settings, [matcher.MatchResult(_cand("https://youtu.be/good"), ["youtube"], 0.99)])

    msg = make_message(text=TRACK_URL)
    await spotify.on_spotify_message(msg, services.bot)

    # Candidate 1 presented: button should be present (excluded has 1 entry).
    assert spotify._matches[USER_ID].excluded == {"https://youtu.be/good"}

    # Rematch -> candidate 2 (excluded = 2), still under the cap.
    async def match2(*a, **k):
        return matcher.MatchResult(_cand("https://youtu.be/alt2"), ["youtube"], 0.98)

    monkeypatch.setattr(spotify.matcher, "find_match", match2)
    await spotify.on_rematch(make_callback(data=f"sp:rematch:{TRACK_ID}", message=msg))
    assert spotify._matches[USER_ID].excluded == {
        "https://youtu.be/good",
        "https://youtu.be/alt2",
    }

    # Rematch -> candidate 3 (excluded = 3) -> the button is now dropped.
    async def match3(*a, **k):
        return matcher.MatchResult(_cand("https://youtu.be/alt3"), ["youtube"], 0.97)

    monkeypatch.setattr(spotify.matcher, "find_match", match3)
    await spotify.on_rematch(make_callback(data=f"sp:rematch:{TRACK_ID}", message=msg))

    last_markup = msg.markups[-1]
    rematch_present = any(
        getattr(btn, "callback_data", "") == f"sp:rematch:{TRACK_ID}"
        for row in last_markup.inline_keyboard
        for btn in row
    )
    assert not rematch_present, "rematch button should be gone after the third candidate"

    # A further rematch press is refused without hitting the matcher again.
    calls = {"n": 0}

    async def match4(*a, **k):
        calls["n"] += 1
        return matcher.MatchResult(_cand("https://youtu.be/alt4"), ["youtube"], 0.96)

    monkeypatch.setattr(spotify.matcher, "find_match", match4)
    cb = make_callback(data=f"sp:rematch:{TRACK_ID}", message=msg)
    await spotify.on_rematch(cb)

    assert calls["n"] == 0
    assert cb.answers and cb.answers[-1][1] is True
    assert get_text("en", "spotify_no_more_matches") in cb.answers[-1][0]


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


async def test_collection_select_tracks(services, make_message, monkeypatch, settings):
    _enable(
        monkeypatch,
        settings,
        spotify_enable_collections=True,
        spotify_client_id="cid",
        spotify_client_secret="sec",
        spotify_max_items=3,
    )

    refs = [SpotifyRef("track", f"id{n}", f"https://open.spotify.com/track/id{n}") for n in range(3)]
    collection = spotify.metadata.CollectionMeta(
        kind="album", spotify_id="album1", name="Test Album", artist_or_owner="Test Artist", total=3
    )

    track_metas = [
        spotify.metadata.TrackMeta(
            spotify_id=f"id{n}",
            kind="track",
            title=f"Song {n}",
            artists=[f"Artist {n}"],
            duration_s=200.0,
        )
        for n in range(3)
    ]

    async def fake_list(*args, **kwargs):
        return refs

    async def fake_meta(*args, **kwargs):
        return collection

    async def fake_track_meta(*args, **kwargs):
        # args[0] is db, args[1] is the SpotifyRef
        ref = args[1] if len(args) > 1 else None
        for m in track_metas:
            if ref and m.spotify_id == ref.id:
                return m
        return track_metas[0]

    monkeypatch.setattr(spotify.metadata, "list_collection_tracks", fake_list)
    monkeypatch.setattr(spotify.metadata, "fetch_collection_meta", fake_meta)
    monkeypatch.setattr(spotify.metadata, "fetch_metadata", fake_track_meta)

    msg = make_message(text=ALBUM_URL)
    await spotify.on_spotify_message(msg, services.bot)

    # Should show the track-selection page.
    assert get_text("en", "spotify_select_tracks", title="Test Album", artist="Test Artist",
                    selected=0, total=3, page=1, pages=1) in msg.edits
    markup = msg.markups[-1]
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "Song 0" in labels[0]
    assert "Song 1" in labels[1]
    assert "Song 2" in labels[2]
    cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert f"sp:sel:album:{TRACK_ID}:0:0" in cbs
    assert f"sp:sel:album:{TRACK_ID}:0:1" in cbs
    assert f"sp:sel:album:{TRACK_ID}:0:2" in cbs
    assert f"sp:zip:album:{TRACK_ID}" in cbs  # Create ZIP button

    # State should be stored for the callback.
    from tests.conftest import USER_ID
    assert USER_ID in spotify._collection_state
    state = spotify._collection_state[USER_ID]
    assert state.ref.id == TRACK_ID
    assert state.ref.kind == "album"
    assert len(state.refs) == 3
    assert state.selected == set()


async def test_collection_toggle_select(services, make_message, monkeypatch, settings):
    _enable(
        monkeypatch,
        settings,
        spotify_enable_collections=True,
        spotify_client_id="cid",
        spotify_client_secret="sec",
        spotify_max_items=3,
    )

    refs = [SpotifyRef("track", f"id{n}", f"https://open.spotify.com/track/id{n}") for n in range(2)]
    collection = spotify.metadata.CollectionMeta(
        kind="album", spotify_id="album1", name="Test Album", artist_or_owner="Test Artist", total=2
    )

    async def fake_list(*args, **kwargs):
        return refs

    async def fake_meta(*args, **kwargs):
        return collection

    monkeypatch.setattr(spotify.metadata, "list_collection_tracks", fake_list)
    monkeypatch.setattr(spotify.metadata, "fetch_collection_meta", fake_meta)

    msg = make_message(text=ALBUM_URL)
    await spotify.on_spotify_message(msg, services.bot)

    from tests.conftest import USER_ID
    state = spotify._collection_state[USER_ID]
    assert state.selected == set()

    # Toggle track id0 on page 0.
    cb_msg = make_message()
    cb = type("CB", (), {"data": f"sp:sel:album:{TRACK_ID}:0:0", "from_user": make_message().from_user,
                         "message": cb_msg, "answers": []})()
    async def fake_answer(*a, **k):
        cb.answers.append(a)
    cb.answer = fake_answer
    await spotify._on_track_select(cb, services.bot)
    assert 0 in state.selected

    # Toggle again to deselect.
    cb2 = type("CB", (), {"data": f"sp:sel:album:{TRACK_ID}:0:0", "from_user": make_message().from_user,
                          "message": cb_msg, "answers": []})()
    cb2.answer = fake_answer
    await spotify._on_track_select(cb2, services.bot)
    assert state.selected == set()


async def test_collection_zip_download(services, make_message, monkeypatch, settings):
    _enable(
        monkeypatch,
        settings,
        spotify_enable_collections=True,
        spotify_client_id="cid",
        spotify_client_secret="sec",
        spotify_max_items=3,
    )

    refs = [SpotifyRef("track", "id1", "https://open.spotify.com/track/id1")]
    collection = spotify.metadata.CollectionMeta(
        kind="album", spotify_id="album1", name="Test Album", artist_or_owner="Test Artist", total=1
    )

    # Prime the collection state with track id1 selected and a pre-downloaded file.
    from tests.conftest import USER_ID
    tmpdir = settings.temp_dir_resolved / f"zip_test_{int(time.time()*1000)}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    track_file = tmpdir / "01 - Test Artist - Never Gonna Give You Up.mp3"
    track_file.write_bytes(b"fake-audio-data")

    collection_ref = SpotifyRef("track", "id1", "https://open.spotify.com/track/id1")
    spotify._collection_state[USER_ID] = spotify._CollectionState(
        user_id=USER_ID,
        ref=collection_ref,
        meta=collection,
        refs=refs,
        tmpdir=tmpdir,
        lang="en",
        status_message=make_message(),
        selected={0},
    )

    # Mock _download_one_track to return the pre-created file.
    async def fake_download_one(srv, track_ref, td, lang, index):
        return track_file

    monkeypatch.setattr(spotify, "_download_one_track", fake_download_one)

    cb_msg = make_message()
    cb = type("CB", (), {"data": "sp:zip:track:id1", "from_user": make_message().from_user,
                         "message": cb_msg, "answers": []})()
    async def fake_answer(*a, **k):
        cb.answers.append(a)
    cb.answer = fake_answer

    await spotify._on_collection_zip(cb, services.bot)

    # A ZIP file should have been sent via the bot.
    assert len(services.bot.documents) == 1
    sent_chat, sent_file, _ = services.bot.documents[0]
    assert sent_chat == USER_ID
    assert "Test Album" in str(sent_file.path)


async def test_collection_zip_cancel(services, make_message, monkeypatch, settings):
    _enable(
        monkeypatch,
        settings,
        spotify_enable_collections=True,
        spotify_client_id="cid",
        spotify_client_secret="sec",
        spotify_max_items=3,
    )

    refs = [SpotifyRef("track", "id1", "https://open.spotify.com/track/id1")]
    collection = spotify.metadata.CollectionMeta(
        kind="album", spotify_id="album1", name="Test Album", artist_or_owner="Test Artist", total=1
    )

    async def fake_list(*args, **kwargs):
        return refs

    async def fake_meta(*args, **kwargs):
        return collection

    async def fake_track_meta_none(*args, **kwargs):
        return None

    monkeypatch.setattr(spotify.metadata, "list_collection_tracks", fake_list)
    monkeypatch.setattr(spotify.metadata, "fetch_collection_meta", fake_meta)
    monkeypatch.setattr(spotify.metadata, "fetch_metadata", fake_track_meta_none)

    msg = make_message(text=ALBUM_URL)
    await spotify.on_spotify_message(msg, services.bot)

    assert get_text("en", "spotify_select_tracks", title="Test Album", artist="Test Artist",
                    selected=0, total=1, page=1, pages=1) in msg.edits

    from tests.conftest import USER_ID
    state = spotify._collection_state[USER_ID]
    state.selected = {0}  # select the track

    # Cancel the ZIP before it starts.
    cb_msg = make_message()
    cb = type("CB", (), {"data": f"sp:zipc:cancel:album:{TRACK_ID}", "from_user": make_message().from_user,
                         "message": cb_msg, "answers": []})()
    async def fake_answer(*a, **k):
        cb.answers.append(a)
    cb.answer = fake_answer

    await spotify._on_zip_control(cb, services.bot)

    assert state.zip_cancelled is True
    assert USER_ID not in spotify._collection_state
