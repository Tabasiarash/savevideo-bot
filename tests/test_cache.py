from __future__ import annotations

from bot.services.cache import DownloadCache


async def test_cache_get_miss_counts_and_returns_none(db, settings):
    cache = DownloadCache(db, settings)
    assert await cache.get("h", "video_720") is None
    _, hits, misses = await db.cache_hit_rate()
    assert (hits, misses) == (0, 1)


async def test_cache_put_then_get(db, settings):
    cache = DownloadCache(db, settings)
    await cache.put("h", "video_720", "FID", "My Title", 4096)
    entry = await cache.get("h", "video_720")
    assert entry is not None
    assert entry.file_id == "FID"
    assert entry.title == "My Title"
    assert entry.size_bytes == 4096
    _, hits, _ = await db.cache_hit_rate()
    assert hits == 1


async def test_cache_refresh_extends(tmp_path, db, monkeypatch):
    from bot.config import settings as s

    monkeypatch.setattr(s, "cache_ttl_days", 1, raising=False)
    cache = DownloadCache(db, s)
    await cache.put("h", "audio_mp3", "FID", "T", 10)
    await cache.refresh("h", "audio_mp3")
    entry = await cache.get("h", "audio_mp3")
    assert entry is not None


async def test_cache_refresh_missing_is_noop(db, settings):
    cache = DownloadCache(db, settings)
    await cache.refresh("nope", "video_720")  # must not raise
