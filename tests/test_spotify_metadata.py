from __future__ import annotations

from types import SimpleNamespace

from bot.services.spotify import metadata
from bot.services.spotify.urls import SpotifyRef

REF = SpotifyRef("track", "4uLU6hMCjMI75M1A2tKUQC", "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC")

OEMBED_JSON = (
    '{"title": "Never Gonna Give You Up", '
    '"thumbnail_url": "https://i.scdn.co/image/oembed.jpg", '
    '"provider_name": "Spotify"}'
)

PAGE_HTML = """
<!doctype html><html><head>
<meta property="og:title" content="Never Gonna Give You Up - song and lyrics by Rick Astley | Spotify" />
<meta property="og:description" content="Rick Astley &#183; Song &#183; 1987" />
<meta property="og:image" content="https://i.scdn.co/image/page.jpg" />
<meta property="music:duration" content="213" />
</head><body></body></html>
"""

PAGE_HTML_UNICODE = """
<html><head>
<meta property="og:title" content="Never Gonna Give You Up - song and lyrics by Rick Astley | Spotify" />
<meta property="og:description" content="Rick Astley · Song · 1987" />
<meta property="og:image" content="https://i.scdn.co/image/page.jpg" />
<meta property="music:duration" content="213" />
</head></html>
"""


def test_from_oembed_basic():
    import json

    meta = metadata.from_oembed(json.loads(OEMBED_JSON), REF)
    assert meta.title == "Never Gonna Give You Up"
    assert meta.cover_url == "https://i.scdn.co/image/oembed.jpg"
    assert meta.provider == "oembed"
    assert meta.is_usable() is True


def test_from_page_parses_tags():
    meta = metadata.from_page(PAGE_HTML_UNICODE, REF)
    assert meta.title == "Never Gonna Give You Up"
    assert meta.artists == ["Rick Astley"]
    assert meta.year == "1987"
    assert meta.duration_s == 213.0
    assert meta.cover_url == "https://i.scdn.co/image/page.jpg"


def test_meta_tags_handles_content_before_property():
    html = '<meta content="X" property="og:title" />'
    tags = metadata._meta_tags(html)
    assert tags["og:title"] == "X"


def test_merge_fills_gaps():
    import json

    base = metadata.from_oembed(json.loads(OEMBED_JSON), REF)
    extra = metadata.from_page(PAGE_HTML_UNICODE, REF)
    merged = metadata._merge(base, extra)
    assert merged.title == "Never Gonna Give You Up"
    assert merged.artists == ["Rick Astley"]
    assert merged.cover_url == "https://i.scdn.co/image/oembed.jpg"
    assert "page" in merged.provider


def test_meta_json_roundtrip():
    meta = metadata.TrackMeta(
        spotify_id="id", kind="track", title="T", artists=["A"], duration_s=12.5, provider="oembed"
    )
    restored = metadata.TrackMeta.from_json(meta.to_json())
    assert restored is not None
    assert restored.title == "T"
    assert restored.artists == ["A"]
    assert restored.duration_s == 12.5
    assert metadata.TrackMeta.from_json("not json") is None


def test_from_api_maps_fields():
    payload = {
        "name": "Song",
        "artists": [{"name": "A"}, {"name": "B"}],
        "album": {"name": "Album", "images": [{"url": "cover"}], "release_date": "1999-05-01"},
        "duration_ms": 210000,
        "external_ids": {"isrc": "US1234567890"},
    }
    meta = metadata.from_api(payload, REF)
    assert meta.title == "Song"
    assert meta.artists == ["A", "B"]
    assert meta.album == "Album"
    assert meta.cover_url == "cover"
    assert meta.year == "1999"
    assert meta.duration_s == 210.0
    assert meta.isrc == "US1234567890"


async def test_fetch_metadata_merges_and_caches(monkeypatch, db):
    calls: list[str] = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        if "oembed" in url:
            return SimpleNamespace(status=200, headers={}, text=OEMBED_JSON, url=url)
        return SimpleNamespace(status=200, headers={}, text=PAGE_HTML_UNICODE, url=url)

    monkeypatch.setattr(metadata, "fetch", fake_fetch)
    meta = await metadata.fetch_metadata(db, REF)
    assert meta is not None
    assert meta.artists == ["Rick Astley"]
    assert len(calls) == 2

    # Second call hits the cache: no further HTTP.
    calls.clear()
    cached = await metadata.fetch_metadata(db, REF)
    assert cached is not None and cached.title == "Never Gonna Give You Up"
    assert calls == []


async def test_fetch_metadata_survives_provider_errors(monkeypatch, db):
    async def boom(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(metadata, "fetch", boom)
    assert await metadata.fetch_metadata(db, REF) is None


async def test_fetch_metadata_uses_api_for_isrc(monkeypatch, db):
    api_payload = (
        '{"name": "Never Gonna Give You Up", "artists": [{"name": "Rick Astley"}], '
        '"external_ids": {"isrc": "GBARL9300135"}}'
    )

    async def fake_fetch(url, **kwargs):
        if "oembed" in url:
            return SimpleNamespace(status=200, headers={}, text=OEMBED_JSON, url=url)
        if "api.spotify.com" in url:
            return SimpleNamespace(status=200, headers={}, text=api_payload, url=url)
        return SimpleNamespace(status=200, headers={}, text="<html></html>", url=url)

    async def fake_post_form(url, data, **kwargs):
        return SimpleNamespace(status=200, headers={}, text='{"access_token": "tok"}', url=url)

    monkeypatch.setattr(metadata, "fetch", fake_fetch)
    monkeypatch.setattr(metadata, "post_form", fake_post_form)
    meta = await metadata.fetch_metadata(db, REF, client_id="cid", client_secret="sec")
    assert meta is not None
    assert meta.isrc == "GBARL9300135"
    assert "api" in meta.provider


async def test_list_collection_requires_credentials(db):
    album = SpotifyRef("album", "abc", "https://open.spotify.com/album/abc")
    assert await metadata.list_collection_tracks(db, album) == []


async def test_list_collection_album(monkeypatch, db):
    album = SpotifyRef("album", "abc", "https://open.spotify.com/album/abc")
    page = '{"items": [{"id": "t1", "type": "track"}, {"id": "t2", "type": "track"}], "next": null}'

    async def fake_post_form(url, data, **kwargs):
        return SimpleNamespace(status=200, headers={}, text='{"access_token": "tok"}', url=url)

    async def fake_fetch(url, **kwargs):
        return SimpleNamespace(status=200, headers={}, text=page, url=url)

    monkeypatch.setattr(metadata, "post_form", fake_post_form)
    monkeypatch.setattr(metadata, "fetch", fake_fetch)
    refs = await metadata.list_collection_tracks(db, album, client_id="c", client_secret="s", limit=10)
    assert [r.id for r in refs] == ["t1", "t2"]
    assert all(r.kind == "track" for r in refs)


async def test_list_collection_playlist_unwraps_track(monkeypatch, db):
    playlist = SpotifyRef("playlist", "p1", "https://open.spotify.com/playlist/p1")
    page = '{"items": [{"track": {"id": "t1", "type": "track"}}], "next": null}'

    async def fake_post_form(url, data, **kwargs):
        return SimpleNamespace(status=200, headers={}, text='{"access_token": "tok"}', url=url)

    async def fake_fetch(url, **kwargs):
        return SimpleNamespace(status=200, headers={}, text=page, url=url)

    monkeypatch.setattr(metadata, "post_form", fake_post_form)
    monkeypatch.setattr(metadata, "fetch", fake_fetch)
    refs = await metadata.list_collection_tracks(db, playlist, client_id="c", client_secret="s", limit=10)
    assert [r.id for r in refs] == ["t1"]
