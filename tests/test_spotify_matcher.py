from __future__ import annotations

from types import SimpleNamespace

from bot.services.breaker import Breaker
from bot.services.spotify import matcher
from bot.services.spotify.metadata import TrackMeta

META = TrackMeta(
    spotify_id="id",
    kind="track",
    title="Never Gonna Give You Up",
    artists=["Rick Astley"],
    duration_s=213.0,
)


def _settings(**overrides):
    base = {
        "spotify_min_score": 0.65,
        "spotify_duration_tolerance_s": 4,
        "proxy_url": "",
        "proxy_url_soundcloud": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class StubRunner:
    def __init__(self, results: dict[str, object]) -> None:
        self._results = results
        self.calls: list[tuple[str, dict]] = []

    async def extract_info(self, url, *, playlist=False, playlist_items=None, cookiejar_dir=None, proxy=None, flat=False):
        self.calls.append((url, {"playlist": playlist, "proxy": proxy, "flat": flat}))
        prefix = url.split(":", 1)[0]
        result = self._results.get(prefix)
        if isinstance(result, Exception):
            raise result
        return result


def _entry(title, uploader, duration, url="https://youtu.be/x"):
    return {"title": title, "uploader": uploader, "duration": duration, "webpage_url": url}


def test_normalize_strips_noise_and_brackets():
    assert matcher.normalize_text("Rick Astley - Never Gonna Give You Up (Official Video)") == (
        "rick astley never gonna give you up"
    )


def test_text_similarity_tolerates_extra_words():
    score = matcher.text_similarity("Never Gonna Give You Up", "Rick Astley Never Gonna Give You Up HD")
    assert score > 0.5


def test_score_perfect_match():
    cand = matcher.Candidate("u", "Never Gonna Give You Up", "Rick Astley", 213.0, "youtube")
    assert matcher.score_candidate(META, cand, 4) > 0.95


def test_score_penalises_duration_drift():
    good = matcher.Candidate("u", "Never Gonna Give You Up", "Rick Astley", 213.0, "youtube")
    bad = matcher.Candidate("u", "Never Gonna Give You Up", "Rick Astley", 300.0, "youtube")
    assert matcher.score_candidate(META, bad, 4) < matcher.score_candidate(META, good, 4)


def test_build_query():
    assert matcher.build_query(META) == "Rick Astley Never Gonna Give You Up"


def test_source_proxy_prefers_soundcloud_override():
    s = _settings(proxy_url="socks5h://base:1", proxy_url_soundcloud="socks5h://sc:2")
    assert matcher.source_proxy(s, "youtube") == "socks5h://base:1"
    assert matcher.source_proxy(s, "soundcloud") == "socks5h://sc:2"
    assert matcher.source_proxy(_settings(), "youtube") is None


async def test_find_match_picks_best_candidate():
    runner = StubRunner(
        {
            "ytsearch5": {
                "entries": [
                    _entry("Some Other Song", "Another Artist", 180, "https://youtu.be/bad"),
                    _entry("Never Gonna Give You Up", "Rick Astley - Topic", 213, "https://youtu.be/good"),
                ]
            }
        }
    )
    result = await matcher.find_match(runner, _settings(), META)
    assert result.candidate is not None
    assert result.candidate.url == "https://youtu.be/good"
    assert result.candidate.score >= 0.65
    assert runner.calls[0][0].startswith("ytsearch5:")
    assert runner.calls[0][1]["flat"] is True
    assert runner.calls[0][1]["playlist"] is True


async def test_find_match_falls_back_to_soundcloud():
    runner = StubRunner(
        {
            "ytsearch5": {"entries": [ _entry("Totally Different", "Nobody", 10, "https://youtu.be/no")]},
            "scsearch5": {"entries": [_entry("Never Gonna Give You Up", "Rick Astley", 213, "https://sc/good")]},
        }
    )
    result = await matcher.find_match(runner, _settings(), META)
    assert result.candidate is not None and result.candidate.url == "https://sc/good"
    assert result.tried == ["youtube", "soundcloud"]


async def test_find_match_rejects_below_threshold():
    runner = StubRunner({"ytsearch5": {"entries": [_entry("Nothing Alike", "Someone", 50, "https://youtu.be/x")]}})
    result = await matcher.find_match(runner, _settings(), META)
    assert result.candidate is None


async def test_find_match_breaker_opens_on_failure():
    breaker = Breaker(threshold=1, cooldown=100)
    runner = StubRunner({"ytsearch5": RuntimeError("boom")})
    result = await matcher.find_match(runner, _settings(), META, breaker=breaker)
    assert result.candidate is None
    assert breaker.is_open("youtube") is True

    # A second call skips the broken source entirely (no new YouTube search).
    yt_before = [c for c in runner.calls if c[0].startswith("ytsearch5")]
    await matcher.find_match(runner, _settings(), META, breaker=breaker)
    yt_after = [c for c in runner.calls if c[0].startswith("ytsearch5")]
    assert len(yt_after) == len(yt_before)


async def test_find_match_skips_unusable_meta():
    empty = TrackMeta(spotify_id="id", kind="track")
    runner = StubRunner({})
    result = await matcher.find_match(runner, _settings(), empty)
    assert result.candidate is None
    assert runner.calls == []


async def test_candidate_from_entry_requires_http_url():
    assert matcher.candidate_from_entry({"title": "x", "url": "notaurl"}, "youtube") is None
    cand = matcher.candidate_from_entry(_entry("t", "u", 1, "https://a/b"), "youtube")
    assert cand is not None and cand.uploader == "u"
