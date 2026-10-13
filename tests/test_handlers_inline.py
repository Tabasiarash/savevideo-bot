from __future__ import annotations

from types import SimpleNamespace

from aiogram.types import (
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedVideo,
)
from bot.db import url_hash
from bot.handlers import inline
from bot.utils import URLBlockedError

USER = 42


class FakeInlineQuery:
    def __init__(self, query: str = "", user_id: int = USER) -> None:
        self.from_user = SimpleNamespace(id=user_id, language_code="en")
        self.query = query
        self.results = None
        self.answers = []

    async def answer(self, results, **kwargs):
        self.results = results
        self.answers.append(kwargs)


async def test_inline_no_url_gives_hint(services, bot):
    q = FakeInlineQuery(query="nothing here")
    await inline.on_inline_query(q, bot)
    assert len(q.results) == 1
    assert q.results[0].id == "hint"


async def test_inline_blocked_url(services, bot, monkeypatch):
    async def blocked(url):
        raise URLBlockedError("nope")

    monkeypatch.setattr(inline, "check_url_safety", blocked)
    q = FakeInlineQuery(query="https://evil.example/x")
    await inline.on_inline_query(q, bot)
    assert q.results[0].id == "blocked"


async def test_inline_cached_video(services, bot):
    url = "https://youtube.com/watch?v=abc123"
    await services.db.cache_put(url_hash(url), "video_best", "FILEID", "Title", 100)
    q = FakeInlineQuery(query=url)
    await inline.on_inline_query(q, bot)
    assert isinstance(q.results[0], InlineQueryResultCachedVideo)
    assert q.results[0].video_file_id == "FILEID"


async def test_inline_cached_audio(services, bot):
    url = "https://youtube.com/watch?v=abc123"
    await services.db.cache_put(url_hash(url), "audio_mp3", "AUDIOID", "Title", 100)
    q = FakeInlineQuery(query=url)
    await inline.on_inline_query(q, bot)
    assert isinstance(q.results[0], InlineQueryResultCachedAudio)
    assert q.results[0].audio_file_id == "AUDIOID"


async def test_inline_cache_miss_creates_deeplink(services, bot):
    url = "https://youtube.com/watch?v=fresh"
    q = FakeInlineQuery(query=url)
    await inline.on_inline_query(q, bot)
    result = q.results[0]
    assert isinstance(result, InlineQueryResultArticle)
    token = result.id.removeprefix("dl_")
    row = await services.db.deeplink_get(token)
    assert row is not None
    assert row["url"] == url
    assert result.url.startswith("https://t.me/testbot?start=dl_")
