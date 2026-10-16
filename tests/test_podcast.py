from __future__ import annotations

from bot.services.podcast import feed, itunes, resolver
from bot.services.podcast.meta import EpisodeMeta
from bot.services.podcast.scoring import duration_score, duration_tolerance, score_candidate
from bot.services.spotify.http import HttpResponse
from bot.services.spotify.matcher import Candidate

# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def test_duration_tolerance_is_percentage_aware():
    # 3% of a long episode dwarfs the 30s floor.
    assert duration_tolerance(4000.0, 30, 0.03) == 120.0
    assert duration_tolerance(60.0, 30, 0.03) == 30.0
    assert duration_tolerance(None, 30, 0.03) is None
    assert duration_tolerance(0, 30, 0.03) is None


def test_duration_score_renormalises_over_window():
    assert duration_score(100.0, 100.0, 30.0) == 1.0
    assert duration_score(100.0, 115.0, 30.0) == 0.5
    assert duration_score(100.0, 200.0, 30.0) == 0.0
    assert duration_score(None, 100.0, 30.0) is None


def test_score_prefers_title_and_show():
    exact = score_candidate(
        title="Episode One",
        show="Great Show",
        duration_s=3600.0,
        cand_title="Episode One",
        cand_show="Great Show",
        cand_duration_s=3600.0,
        tol_min_s=30,
        tol_pct=0.03,
    )
    wrong = score_candidate(
        title="Episode One",
        show="Great Show",
        duration_s=3600.0,
        cand_title="Something Else",
        cand_show="Another Show",
        cand_duration_s=3600.0,
        tol_min_s=30,
        tol_pct=0.03,
    )
    assert exact == 1.0
    assert wrong < exact


def test_non_latin_title_clears_min_score():
    # A Persian episode whose title and show match exactly must score near 1.0,
    # not zero; otherwise iTunes/feed results are wrongly discarded.
    score = score_candidate(
        title="قسمت ۸۱: انسان و بتن",
        show="Sekke Podcast | پادکست سکه",
        duration_s=4804.47,
        cand_title="قسمت ۸۱: انسان و بتن",
        cand_show="Sekke Podcast | پادکست سکه",
        cand_duration_s=4805.0,
        tol_min_s=30,
        tol_pct=0.03,
    )
    assert score > 0.9


def test_score_folds_script_and_digit_variants():
    # Candidate from a source that spells the title differently (Arabic yeh,
    # Persian digits) must still score near-perfectly.
    score = score_candidate(
        title="قسمت ۸۱: انسان و بتن",
        show="پادکست سکه",
        duration_s=4804.0,
        cand_title="قسمت 81 انسان و بتن",
        cand_show="پادكست سكه",
        cand_duration_s=4805.0,
        tol_min_s=30,
        tol_pct=0.03,
    )
    assert score > 0.9


def test_score_supports_cjk():
    score = score_candidate(
        title="東京ポッドキャスト 第5回",
        show="東京ポッドキャスト",
        duration_s=1800.0,
        cand_title="東京ポッドキャスト 第5回",
        cand_show="東京ポッドキャスト",
        cand_duration_s=1801.0,
        tol_min_s=30,
        tol_pct=0.03,
    )
    assert score > 0.9


# --------------------------------------------------------------------------
# feed parsing
# --------------------------------------------------------------------------

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>My Show</title>
    <item>
      <title>Episode One</title>
      <guid>ep-1</guid>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <itunes:duration>1:02:03</itunes:duration>
      <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg" length="123"/>
    </item>
    <item>
      <title>Not audio</title>
      <enclosure url="https://cdn.example.com/pic.jpg" type="image/jpeg"/>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Show</title>
  <entry>
    <title>Atom Episode</title>
    <id>tag:example,2024:1</id>
    <updated>2024-02-02T10:00:00Z</updated>
    <link rel="enclosure" href="https://cdn.example.com/a.m4a" type="audio/mp4"/>
  </entry>
</feed>
"""


def test_parse_rss_enclosure_and_duration():
    items = feed.parse_feed(RSS, max_items=10)
    assert len(items) == 1  # the image enclosure is skipped
    item = items[0]
    assert item.title == "Episode One"
    assert item.audio_url == "https://cdn.example.com/ep1.mp3"
    assert item.duration_s == 3723.0
    assert item.guid == "ep-1"
    assert item.released and item.released.startswith("Mon")


def test_parse_atom_enclosure():
    items = feed.parse_feed(ATOM, max_items=10)
    assert len(items) == 1
    assert items[0].audio_url == "https://cdn.example.com/a.m4a"


def test_parse_feed_is_defensive_and_capped():
    assert feed.parse_feed(b"<not xml", max_items=5) == []
    # Billion-laughs style input must not expand.
    bomb = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [<!ENTITY lol "lol">]>
    <rss><channel><item><title>&lol;</title>
    <enclosure url="https://x/a.mp3" type="audio/mpeg"/></item></channel></rss>"""
    items = feed.parse_feed(bomb, max_items=5)
    assert all("lol" * 5 not in (i.title or "") for i in items)


def test_parse_feed_honours_item_cap():
    many = b"<rss><channel>" + b"".join(
        f'<item><title>E{i}</title><enclosure url="https://x/{i}.mp3" type="audio/mpeg"/></item>'.encode()
        for i in range(50)
    ) + b"</channel></rss>"
    assert len(feed.parse_feed(many, max_items=7)) == 7


async def test_list_enclosures_fetches_and_parses(monkeypatch):
    async def fake_fetch(url, **kwargs):
        assert kwargs["max_bytes"] == 1234
        return HttpResponse(status=200, headers={}, text="", url=url, content=RSS)

    monkeypatch.setattr(feed, "fetch", fake_fetch)
    items = await feed.list_enclosures("https://show/feed.xml", max_bytes=1234, max_items=10)
    assert len(items) == 1
    assert items[0].audio_url == "https://cdn.example.com/ep1.mp3"


async def test_list_enclosures_rejects_non_http():
    assert await feed.list_enclosures("file:///etc/passwd", max_bytes=10, max_items=10) == []


# --------------------------------------------------------------------------
# itunes
# --------------------------------------------------------------------------

_ITUNES_JSON = (
    b'{"resultCount":1,"results":[{'
    b'"trackName":"Episode One","collectionName":"My Show",'
    b'"episodeUrl":"https://cdn.example.com/ep1.mp3",'
    b'"trackTimeMillis":3723000,"feedUrl":"https://show/feed.xml",'
    b'"artworkUrl600":"https://is1.mzstatic.com/cover.jpg",'
    b'"releaseDate":"2024-01-01T00:00:00Z","episodeGuid":"ep-1"}]}'
)

META = EpisodeMeta(spotify_id="0" * 22, title="Episode One", show="My Show", duration_s=3723.0)


async def test_search_episode_scores_and_parses(db, settings, monkeypatch):
    async def fake_fetch(url, **kwargs):
        return HttpResponse(status=200, headers={}, text=_ITUNES_JSON.decode(), url=url, content=_ITUNES_JSON)

    monkeypatch.setattr(itunes, "fetch", fake_fetch)
    monkeypatch.setattr(settings, "podcast_regions", "us", raising=False)
    results = await itunes.search_episode(db, META, proxy=None, settings=settings)
    assert len(results) == 1
    cand = results[0]
    assert cand.audio_url == "https://cdn.example.com/ep1.mp3"
    assert cand.duration_s == 3723.0
    assert cand.feed_url == "https://show/feed.xml"
    assert cand.score > 0.9


async def test_itunes_rate_limit_sets_cooldown(db, settings, monkeypatch):
    calls: list[str] = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        return HttpResponse(status=429, headers={}, text="", url=url)

    monkeypatch.setattr(itunes, "fetch", fake_fetch)
    monkeypatch.setattr(settings, "podcast_regions", "us", raising=False)
    assert await itunes.search_episode(db, META, proxy=None, settings=settings) == []
    assert await db.podcast_itunes_blocked_until("us") > 0
    # Second call must skip the cooling-down storefront (no new request).
    assert await itunes.search_episode(db, META, proxy=None, settings=settings) == []
    assert len(calls) == 1


# --------------------------------------------------------------------------
# resolver
# --------------------------------------------------------------------------


async def _no_itunes(db, meta, *, proxy, settings):
    return []


async def test_resolver_prefers_itunes(db, settings, monkeypatch):
    cand = itunes.ItunesEpisode(
        title="Episode One",
        show="My Show",
        duration_s=3723.0,
        audio_url="https://cdn.example.com/ep1.mp3",
        score=0.95,
    )

    async def fake_search(*a, **k):
        return [cand]

    monkeypatch.setattr(resolver, "search_episode", fake_search)
    resolved = await resolver.resolve_episode_audio(db, None, settings, META, _ref())
    assert resolved is not None
    assert resolved.source == "itunes"
    assert resolved.is_fallback is False
    # Cached for a repeat lookup.
    assert await db.podcast_audio_get(_ref().id, resolver.RESOLVED_TTL_S) is not None


async def test_resolver_falls_back_to_rss(db, settings, monkeypatch):
    monkeypatch.setattr(resolver, "search_episode", _no_itunes)
    monkeypatch.setattr(settings, "podcast_regions", "us", raising=False)
    monkeypatch.setattr(
        resolver,
        "list_enclosures",
        _fake_enclosures([feed.FeedItem(title="Episode One", audio_url="https://cdn/ep1.mp3", duration_s=3723.0)]),
    )
    meta = EpisodeMeta(spotify_id=_ref().id, title="Episode One", show="My Show", duration_s=3723.0, feed_url="https://show/feed.xml")
    resolved = await resolver.resolve_episode_audio(db, None, settings, meta, _ref())
    assert resolved is not None and resolved.source == "rss"


async def test_resolver_last_resort_matcher(db, settings, monkeypatch):
    monkeypatch.setattr(resolver, "search_episode", _no_itunes)

    async def fake_fallback(*a, **k):
        return Candidate("https://youtu.be/x", "Episode One", "My Show", 3723.0, "youtube", 0.9), ["youtube"]

    monkeypatch.setattr(resolver, "find_fallback_match", fake_fallback)
    meta = EpisodeMeta(spotify_id=_ref().id, title="Episode One", show="My Show", duration_s=3723.0)
    resolved = await resolver.resolve_episode_audio(db, None, settings, meta, _ref())
    assert resolved is not None
    assert resolved.source == "youtube"
    assert resolved.is_fallback is True


async def test_resolver_returns_none_when_nothing_found(db, settings, monkeypatch):
    monkeypatch.setattr(resolver, "search_episode", _no_itunes)

    async def fake_fallback(*a, **k):
        return None, ["youtube"]

    monkeypatch.setattr(resolver, "find_fallback_match", fake_fallback)
    meta = EpisodeMeta(spotify_id=_ref().id, title="Episode One", show="My Show")
    assert await resolver.resolve_episode_audio(db, None, settings, meta, _ref()) is None


def _ref():
    from bot.services.spotify.urls import SpotifyRef

    return SpotifyRef(kind="episode", id="3x4y5z6a7b8c9d0e1f2g3h", canonical="https://open.spotify.com/episode/3x4y5z6a7b8c9d0e1f2g3h")


def _fake_enclosures(items):
    async def _inner(url, **kwargs):
        return items

    return _inner
