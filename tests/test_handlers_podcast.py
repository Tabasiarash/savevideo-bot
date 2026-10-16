from __future__ import annotations

from bot.handlers import links, spotify
from bot.i18n import get_text
from bot.services.podcast.meta import EpisodeMeta
from bot.services.podcast.resolver import ResolvedEpisode

EPISODE_ID = "5o3CeI4Dz5eNzYWDwm08pG"
EPISODE_URL = f"https://open.spotify.com/episode/{EPISODE_ID}"
SHOW_ID = "4rOoJ6Egrf8K2IrywzwOMk"
SHOW_URL = f"https://open.spotify.com/show/{SHOW_ID}"

META = EpisodeMeta(
    spotify_id=EPISODE_ID,
    title="Episode One",
    show="Sekke Podcast",
    duration_s=3600.0,
    cover_url="https://i.scdn.co/image/cover.jpg",
    released="2024-01-01",
)

ENCLOSURE = ResolvedEpisode(
    audio_url="https://cdn.example.com/ep1.mp3",
    source="itunes",
    title="Episode One",
    show="Sekke Podcast",
    duration_s=3600.0,
    artwork_url="https://is1.mzstatic.com/cover.jpg",
    is_fallback=False,
)


def _enable(monkeypatch, settings, **overrides):
    monkeypatch.setattr(settings, "enable_podcasts", True, raising=False)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value, raising=False)


def _stub_meta(monkeypatch, value=META):
    async def fake(*args, **kwargs):
        return value

    monkeypatch.setattr(spotify.podcast, "fetch_episode_meta", fake)


def _stub_resolve(monkeypatch, value):
    async def fake(*args, **kwargs):
        return value

    monkeypatch.setattr(spotify.podcast, "resolve_episode_audio", fake)


# -- gating -----------------------------------------------------------------


async def test_episode_disabled_replies_and_stops(services, make_message, monkeypatch, settings):
    monkeypatch.setattr(settings, "enable_podcasts", False, raising=False)
    monkeypatch.setattr(settings, "enable_spotify", False, raising=False)
    msg = make_message(text=EPISODE_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "podcast_disabled") in msg.sent
    assert links._pending == {}
    assert services.runner.info_calls == []  # never reached the catch-all


async def test_show_link_when_enabled(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)
    msg = make_message(text=SHOW_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "podcast_show_link") in msg.sent


async def test_show_link_when_podcasts_off_falls_through(services, make_message, monkeypatch, settings):
    monkeypatch.setattr(settings, "enable_podcasts", False, raising=False)
    monkeypatch.setattr(settings, "enable_spotify", False, raising=False)
    msg = make_message(text=SHOW_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "spotify_disabled") in msg.sent


async def test_filter_matches_episode_links(make_message):
    flt = spotify.SpotifyLinkFilter()
    assert await flt(make_message(text=EPISODE_URL)) is True
    assert await flt(make_message(text=SHOW_URL)) is True


# -- flow -------------------------------------------------------------------


async def test_episode_no_metadata(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)
    _stub_meta(monkeypatch, None)
    msg = make_message(text=EPISODE_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "podcast_no_metadata") in msg.edits


async def test_episode_too_long(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings, podcast_max_duration_min=10)
    _stub_meta(monkeypatch)
    msg = make_message(text=EPISODE_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "podcast_too_long", max=10) in msg.edits


async def test_episode_no_match(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)
    _stub_meta(monkeypatch)
    _stub_resolve(monkeypatch, None)
    msg = make_message(text=EPISODE_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "podcast_no_match") in msg.edits


async def test_episode_fallback_presents_labelled_picker(services, make_message, monkeypatch, settings):
    from tests.conftest import USER_ID

    _enable(monkeypatch, settings)
    _stub_meta(monkeypatch)
    fallback = ResolvedEpisode(
        audio_url="https://youtu.be/x", source="youtube", title="Episode One", show="Sekke Podcast", duration_s=3600.0, is_fallback=True
    )
    _stub_resolve(monkeypatch, fallback)

    msg = make_message(text=EPISODE_URL)
    await spotify.on_spotify_message(msg, services.bot)

    flow = links._pending.get(USER_ID)
    assert flow is not None
    assert flow.url == "https://youtu.be/x"
    assert flow.url_site == "youtube"
    assert "youtube" in msg.edits[-1]
    assert msg.markups[-1] is not None


async def test_episode_enclosure_delivers_audio(services, make_message, monkeypatch, settings, bot):
    from tests.conftest import USER_ID

    _enable(monkeypatch, settings)
    _stub_meta(monkeypatch)
    _stub_resolve(monkeypatch, ENCLOSURE)

    async def fake_stream(url, dest_dir, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "episode.mp3"
        path.write_bytes(b"AUDIO-BYTES")
        return path

    async def fake_artwork(url, dest):
        return None

    async def fake_tag(*args, **kwargs):
        return False  # skip ffmpeg in tests

    monkeypatch.setattr(spotify.podcast, "stream_enclosure", fake_stream)
    monkeypatch.setattr(spotify, "_download_artwork", fake_artwork)
    monkeypatch.setattr(spotify.tagger, "tag_audio", fake_tag)

    msg = make_message(text=EPISODE_URL)
    await spotify.on_spotify_message(msg, services.bot)

    assert len(bot.audios) == 1
    chat_id, file, kwargs = bot.audios[0]
    assert chat_id == USER_ID
    assert kwargs.get("title") == "Episode One"
    assert kwargs.get("performer") == "Sekke Podcast"
    assert links._pending == {}


async def test_stream_enclosure_too_large_is_reported(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)
    _stub_meta(monkeypatch)
    _stub_resolve(monkeypatch, ENCLOSURE)

    async def fake_stream(url, dest_dir, **kwargs):
        raise spotify.podcast.EnclosureTooLarge("too big")

    monkeypatch.setattr(spotify.podcast, "stream_enclosure", fake_stream)
    msg = make_message(text=EPISODE_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "podcast_too_large", max=settings.podcast_max_enclosure_mb) in msg.edits


async def test_stream_enclosure_error_is_reported(services, make_message, monkeypatch, settings):
    _enable(monkeypatch, settings)
    _stub_meta(monkeypatch)
    _stub_resolve(monkeypatch, ENCLOSURE)

    async def fake_stream(url, dest_dir, **kwargs):
        raise spotify.podcast.EnclosureError("boom")

    monkeypatch.setattr(spotify.podcast, "stream_enclosure", fake_stream)
    msg = make_message(text=EPISODE_URL)
    await spotify.on_spotify_message(msg, services.bot)
    assert get_text("en", "podcast_download_failed") in msg.edits
