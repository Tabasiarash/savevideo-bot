from __future__ import annotations

from bot.services.spotify import http, urls


class FakeResponse:
    def __init__(self, status, headers=None, chunks=None, url="https://x"):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks or []
        self.url = url
        self.closed = False

    def iter_content(self):
        yield from self._chunks

    def close(self):
        self.closed = True


def _install(monkeypatch, handler):
    import curl_cffi.requests as requests

    monkeypatch.setattr(requests, "get", handler)


async def test_fetch_reads_and_caps(monkeypatch):
    def fake_get(url, **kwargs):
        return FakeResponse(200, {}, [b"a" * 10, b"b" * 10], url=url)

    _install(monkeypatch, fake_get)
    resp = await http.fetch("https://open.spotify.com/x", max_bytes=5)
    assert resp.status == 200
    assert resp.content == b"aaaaa"
    assert resp.text == "aaaaa"


async def test_fetch_follows_allowed_redirect(monkeypatch):
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        assert kwargs["allow_redirects"] is False  # manual hop control
        assert kwargs["stream"] is True
        if url.endswith("/a"):
            return FakeResponse(302, {"Location": "https://open.spotify.com/b"}, url=url)
        return FakeResponse(200, {}, [b"ok"], url=url)

    _install(monkeypatch, fake_get)
    resp = await http.fetch("https://open.spotify.com/a", allow_redirects=True, is_allowed=urls.is_spotify_host)
    assert resp.status == 200
    assert resp.content == b"ok"
    assert seen == ["https://open.spotify.com/a", "https://open.spotify.com/b"]


async def test_fetch_refuses_redirect_off_allowlist(monkeypatch):
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return FakeResponse(302, {"Location": "https://evil.example/steal"}, url=url)

    _install(monkeypatch, fake_get)
    resp = await http.fetch("https://open.spotify.com/a", allow_redirects=True, is_allowed=urls.is_spotify_host)
    assert resp.status == 302
    assert resp.content == b""
    # The disallowed hop was never requested.
    assert seen == ["https://open.spotify.com/a"]


async def test_fetch_redirect_budget(monkeypatch):
    def fake_get(url, **kwargs):
        return FakeResponse(302, {"Location": "https://open.spotify.com/loop"}, url=url)

    _install(monkeypatch, fake_get)
    resp = await http.fetch(
        "https://open.spotify.com/loop",
        allow_redirects=True,
        is_allowed=urls.is_spotify_host,
        max_redirects=3,
    )
    assert resp.status == 302


async def test_fetch_auto_redirect_without_predicate(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["allow_redirects"] = kwargs["allow_redirects"]
        return FakeResponse(200, {}, [b"z"], url=url)

    _install(monkeypatch, fake_get)
    resp = await http.fetch("https://open.spotify.com/x", allow_redirects=True)
    assert resp.content == b"z"
    assert captured["allow_redirects"] is True


async def test_fetch_no_redirect_by_default(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["allow_redirects"] = kwargs["allow_redirects"]
        return FakeResponse(302, {"Location": "https://open.spotify.com/b"}, url=url)

    _install(monkeypatch, fake_get)
    resp = await http.fetch("https://open.spotify.com/a")
    assert resp.status == 302
    assert captured["allow_redirects"] is False


async def test_post_form_caps(monkeypatch):
    import curl_cffi.requests as requests

    def fake_post(url, data=None, **kwargs):
        assert kwargs["stream"] is True
        return FakeResponse(200, {}, [b"x" * 100], url=url)

    monkeypatch.setattr(requests, "post", fake_post)
    resp = await http.post_form("https://accounts.spotify.com/api/token", {"a": "b"}, max_bytes=8)
    assert resp.content == b"x" * 8


def test_cdn_and_api_predicates():
    assert urls.is_spotify_cdn("https://i.scdn.co/image/abc")
    assert urls.is_spotify_cdn("https://image-cdn-ak.spotifycdn.com/image/abc")
    assert not urls.is_spotify_cdn("https://evil.example/i.scdn.co")
    assert urls.is_spotify_api("https://api.spotify.com/v1/tracks/x")
    assert urls.is_spotify_api("https://accounts.spotify.com/api/token")
    assert not urls.is_spotify_api("https://open.spotify.com/track/x")
    assert urls.is_spotify_host("https://open.spotify.com/track/x")
    assert not urls.is_spotify_host("https://api.spotify.com/v1/tracks/x")
