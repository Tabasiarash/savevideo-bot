from __future__ import annotations

import time

from bot.db import url_hash


def test_url_hash_is_stable():
    a = url_hash("https://youtube.com/watch?v=abc")
    assert a == url_hash("https://youtube.com/watch?v=abc")
    assert a != url_hash("https://youtube.com/watch?v=xyz")
    assert len(a) == 64


async def test_schema_version_recorded(db):
    assert db._conn is not None
    cur = await db._conn.execute("SELECT MAX(version) AS v FROM schema_version")
    row = await cur.fetchone()
    assert int(row["v"]) >= 1


async def test_user_lifecycle(db):
    await db.upsert_user(1, "alice", 1000)
    assert await db.total_users() == 1
    row = await db.get_user(1)
    assert row is not None and row["username"] == "alice"

    await db.upsert_user(1, "alice2", time.time())
    row = await db.get_user(1)
    assert row["username"] == "alice2"
    assert await db.total_users() == 1

    await db.set_language(1, "fa")
    assert await db.get_language(1) == "fa"
    await db.touch_user(1)


async def test_language_missing_returns_none(db):
    await db.upsert_user(2, "bob", int(time.time()))
    assert await db.get_language(2) is None


async def test_ban_and_unban(db):
    await db.upsert_user(1, "u", int(time.time()))
    await db.upsert_user(2, "v", int(time.time()))
    await db.set_banned(1, True)
    assert await db.is_banned(1) is True
    assert await db.is_banned(2) is False
    assert await db.all_user_ids(include_banned=False) == [2]
    assert sorted(await db.all_user_ids(include_banned=True)) == [1, 2]
    await db.set_banned(1, False)
    assert await db.is_banned(1) is False


async def test_record_download_and_stats(db):
    await db.upsert_user(1, "u", int(time.time()))
    await db.record_download(1, url_hash("https://a"), "youtube", "video_720", "ok", None, 100, 10)
    await db.record_download(1, url_hash("https://b"), "instagram", "video_best", "ok", None, 200, 20)
    await db.record_download(1, url_hash("https://c"), "youtube", "audio_mp3", "error", "ytdlp:DownloadError", None, None)
    assert await db.downloads_total() == 3
    assert await db.downloads_today() == 3
    assert await db.thumbnails_total() == 0
    assert dict(await db.top_sites(5)) == {"instagram": 1, "youtube": 2}
    failures = dict(await db.failures_by_type(10))
    assert failures["ytdlp:DownloadError"] == 1


async def test_active_users_and_touch(db):
    await db.upsert_user(1, "u", int(time.time()))
    assert await db.active_users_since(3600) == 1
    assert await db.active_users_since(-1) == 0
    await db.mark_inactive(1)
    assert await db.active_users_since(3600) == 0


async def test_new_users_since(db):
    now = int(time.time())
    await db.upsert_user(1, "recent", now)
    await db.upsert_user(2, "old", now - 10 * 24 * 3600)
    assert await db.new_users_since(24 * 3600) == 1
    assert await db.new_users_since(30 * 24 * 3600) == 2
    assert await db.new_users_since(-1) == 0


async def test_recent_users_ordering_and_limit(db):
    now = int(time.time())
    await db.upsert_user(1, "oldest", now - 100)
    await db.upsert_user(2, "newest", now)
    await db.upsert_user(3, "middle", now - 50)
    rows = await db.recent_users(2)
    assert [int(r["id"]) for r in rows] == [2, 3]
    await db.set_banned(2, True)
    row = (await db.recent_users(1))[0]
    assert row["username"] == "newest" and int(row["banned"]) == 1


async def test_cache_roundtrip_and_expiry(db):
    await db.cache_put("h", "video_720", "FILEID", "Title", 1234)
    row = await db.cache_get("h", "video_720", 3600)
    assert row is not None
    assert row["file_id"] == "FILEID"
    assert row["size_bytes"] == 1234
    assert await db.cache_get("h", "video_720", -10) is None
    # Re-putting updates in place.
    await db.cache_put("h", "video_720", "FILEID2", "Title", 4321)
    row = await db.cache_get("h", "video_720", 3600)
    assert row["file_id"] == "FILEID2"


async def test_cache_purge_expired(db):
    await db.cache_put("old", "video_720", "F", "T", 1)
    removed = await db.purge_cache_expired(-1)
    assert removed == 1
    assert await db.cache_get("old", "video_720", 3600) is None


async def test_counters_and_hit_rate(db):
    await db.counter_incr("cache:hit")
    await db.counter_incr("cache:hit")
    await db.counter_incr("cache:miss")
    await db.counter_incr("cache:miss", by=2)
    rate, hits, misses = await db.cache_hit_rate()
    assert (hits, misses) == (2, 3)
    assert rate == 0.4
    assert await db._counter_get("missing") == 0


async def test_deeplink_lifecycle(db):
    await db.deeplink_create("tok", "https://example.com/x")
    row = await db.deeplink_get("tok")
    assert row["url"] == "https://example.com/x"
    await db.deeplink_delete("tok")
    assert await db.deeplink_get("tok") is None


async def test_deeplink_purge(db):
    await db.deeplink_create("tok", "https://example.com/x")
    assert await db.purge_deeplinks(10**9) == 0
    assert await db.purge_deeplinks(-1) == 1


async def test_get_user_missing(db):
    assert await db.get_user(12345) is None
    assert await db.get_language(12345) is None


async def test_spotify_meta_roundtrip_and_ttl(db):
    assert await db.spotify_meta_get("abc", 3600) is None
    await db.spotify_meta_put("abc", "track", '{"title": "X"}')
    assert await db.spotify_meta_get("abc", 3600) == '{"title": "X"}'
    # A negative TTL means the row is already stale.
    assert await db.spotify_meta_get("abc", -10) is None
    # Re-putting updates in place.
    await db.spotify_meta_put("abc", "track", '{"title": "Y"}')
    assert await db.spotify_meta_get("abc", 3600) == '{"title": "Y"}'


async def test_spotify_meta_purge(db):
    await db.spotify_meta_put("old", "track", "{}")
    assert await db.spotify_meta_purge_expired(10**9) == 0
    assert await db.spotify_meta_purge_expired(-1) == 1


async def test_schema_version_includes_v3(db):
    assert db._conn is not None
    cur = await db._conn.execute("SELECT MAX(version) AS v FROM schema_version")
    row = await cur.fetchone()
    assert int(row["v"]) >= 3

