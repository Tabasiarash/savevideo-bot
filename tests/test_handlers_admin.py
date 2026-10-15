from __future__ import annotations

from types import SimpleNamespace

from bot.handlers import admin

ADMIN = 999
NON_ADMIN = 43


def _command(args: str = "") -> SimpleNamespace:
    return SimpleNamespace(args=args)


async def test_stats_denied_for_non_admin(services, make_message):
    msg = make_message(user_id=NON_ADMIN)
    await admin.cmd_stats(msg)
    assert "not an admin" in msg.sent[0].lower()


async def test_stats_for_admin(services, make_message):
    await services.db.upsert_user(1, "u", 0)
    await services.db.record_download(1, "h", "youtube", "video_720", "ok", None, 5, 1)
    msg = make_message(user_id=ADMIN)
    await admin.cmd_stats(msg)
    assert msg.sent and "user" in msg.sent[0].lower()


async def test_stats_reports_new_users(services, make_message):
    import time

    await services.db.upsert_user(1, "new", int(time.time()))
    await services.db.upsert_user(2, "old", int(time.time()) - 30 * 24 * 3600)
    msg = make_message(user_id=ADMIN)
    await admin.cmd_stats(msg)
    body = msg.sent[0]
    assert "New users: 1 today" in body


async def test_users_denied_for_non_admin(services, make_message):
    msg = make_message(user_id=NON_ADMIN)
    await admin.cmd_users(msg, _command(""))
    assert "not an admin" in msg.sent[0].lower()


async def test_users_lists_recent(services, make_message):
    import time

    now = int(time.time())
    await services.db.upsert_user(1, "alice", now - 100)
    await services.db.upsert_user(2, "bob", now)
    msg = make_message(user_id=ADMIN)
    await admin.cmd_users(msg, _command(""))
    body = msg.sent[0]
    assert "👥" in body and "2 total" in body
    assert "bob" in body and "alice" in body
    # Newest first.
    assert body.index("bob") < body.index("alice")


async def test_users_empty(services, make_message):
    msg = make_message(user_id=ADMIN)
    await admin.cmd_users(msg, _command(""))
    assert "No users yet" in msg.sent[0]


async def test_users_limit_clamped(services, make_message):
    for uid in range(1, 5):
        await services.db.upsert_user(uid, f"u{uid}", uid)
    msg = make_message(user_id=ADMIN)
    await admin.cmd_users(msg, _command("2"))
    body = msg.sent[0]
    assert "Showing 2" in body


async def test_broadcast_requires_text(services, make_message):
    msg = make_message(user_id=ADMIN)
    await admin.cmd_broadcast(msg, _command(""))
    assert "broadcast" in msg.sent[0].lower() or msg.sent


async def test_broadcast_sends_and_marks_blocked(services, bot, make_message):
    await services.db.upsert_user(1, "a", 0)
    await services.db.upsert_user(2, "b", 0)
    bot.raise_for.add(2)  # this user blocked the bot
    msg = make_message(user_id=ADMIN)
    await admin.cmd_broadcast(msg, _command("hello all"))
    assert await services.db.active_users_since(10**9) == 1


async def test_ban_and_unban(services, make_message):
    await services.db.upsert_user(5, "u", 0)
    await admin.cmd_ban(make_message(user_id=ADMIN), _command("5"))
    assert await services.db.is_banned(5) is True
    await admin.cmd_unban(make_message(user_id=ADMIN), _command("5"))
    assert await services.db.is_banned(5) is False


async def test_ban_rejects_bad_arg(services, make_message):
    msg = make_message(user_id=ADMIN)
    await admin.cmd_ban(msg, _command("not-an-id"))
    assert msg.sent


async def test_update_ytdlp(services, make_message):
    msg = make_message(user_id=ADMIN)
    await admin.cmd_update_ytdlp(msg)
    assert services.updater.updates == 1
    assert msg.sent


async def test_health(services, make_message, monkeypatch):
    async def fake_version():
        return "2026.01.01"

    monkeypatch.setattr(admin, "cli_version", fake_version)
    monkeypatch.setattr(admin, "_ffmpeg_version", lambda: "ffmpeg version test")
    monkeypatch.setattr(admin, "detect_js_runtime", lambda s: "deno")
    monkeypatch.setattr(admin, "curl_cffi_version", lambda: "0.13.0")
    msg = make_message(user_id=ADMIN)
    await admin.cmd_health(msg)
    body = msg.sent[0]
    assert "yt-dlp" in msg.sent[0]
    for expected in (
        "Proxy: off",
        "Cookies: none",
        "JS runtime: deno",
        "curl_cffi: 0.13.0",
        "Egress: ✅",
        "Proxy exit: ❌ proxy off",
        "Mode: cloud",
        "Upload limit: 50 MB",
        "Bot API: cloud",
        "Data dir: n/a",
        "Spotify: off",
    ):
        assert expected in body


async def test_health_local_mode_reports_api_and_data_dir(
    services, make_message, monkeypatch, tmp_path
):
    async def fake_version():
        return "2026.01.01"

    monkeypatch.setattr(admin, "cli_version", fake_version)
    monkeypatch.setattr(admin, "_ffmpeg_version", lambda: "ffmpeg version test")
    monkeypatch.setattr(admin.settings, "bot_api_url", "http://127.0.0.1:8081")
    monkeypatch.setattr(admin.settings, "bot_api_data_dir", str(tmp_path))
    msg = make_message(user_id=ADMIN)
    await admin.cmd_health(msg)
    body = msg.sent[0]
    assert "Mode: local" in body
    assert "Bot API: ok @testbot" in body
    assert "Data dir: n/a" not in body


async def test_health_masks_proxy_and_reports_failure(services, make_message, monkeypatch):
    async def fake_version():
        return "2026.01.01"

    async def failing_probe(url=None):
        return SimpleNamespace(ok=False, detail="bot-wall (needs cookies or a different IP)")

    monkeypatch.setattr(admin, "cli_version", fake_version)
    monkeypatch.setattr(admin, "_ffmpeg_version", lambda: "ffmpeg version test")
    monkeypatch.setattr(admin.settings, "proxy_url", "socks5h://user:secret@100.64.0.5:1080")
    monkeypatch.setattr(services.runner, "probe_egress", failing_probe)
    msg = make_message(user_id=ADMIN)
    await admin.cmd_health(msg)
    body = msg.sent[0]
    assert "secret" not in body
    assert "***@100.64.0.5:1080" in body
    assert "bot-wall" in body


async def test_health_reports_platform_proxy_and_exit_ip(services, make_message, monkeypatch):
    async def fake_version():
        return "2026.01.01"

    async def ok_proxy_exit():
        return SimpleNamespace(ok=True, detail="203.0.*.*")

    monkeypatch.setattr(admin, "cli_version", fake_version)
    monkeypatch.setattr(admin, "_ffmpeg_version", lambda: "ffmpeg version test")
    monkeypatch.setattr(admin.settings, "proxy_url", "socks5h://base:1080")
    monkeypatch.setattr(admin.settings, "proxy_url_tiktok", "socks5h://tt:1080")
    monkeypatch.setattr(services.runner, "probe_proxy_egress", ok_proxy_exit)
    msg = make_message(user_id=ADMIN)
    await admin.cmd_health(msg)
    body = msg.sent[0]
    assert "tt=socks5h://tt:1080" in body
    assert "Proxy exit: ✅ 203.0.*.*" in body


async def test_spotify_status(monkeypatch):
    monkeypatch.setattr(admin.settings, "enable_spotify", False)
    assert admin._spotify_status() == "off"
    monkeypatch.setattr(admin.settings, "enable_spotify", True)
    monkeypatch.setattr(admin.settings, "spotify_match_sources", "youtube,soundcloud")
    monkeypatch.setattr(admin.settings, "spotify_client_id", "")
    assert admin._spotify_status() == "on [youtube,soundcloud] no-api"
    monkeypatch.setattr(admin.settings, "spotify_client_id", "cid")
    assert admin._spotify_status() == "on [youtube,soundcloud] api"


async def test_cleanup_denied_for_non_admin(services, make_message):
    msg = make_message(user_id=NON_ADMIN)
    await admin.cmd_cleanup(msg, _command(""))
    assert "not an admin" in msg.sent[0].lower()


async def test_cleanup_removes_temp_entries(services, make_message):
    temp_dir = admin.settings.temp_dir_resolved
    stale = temp_dir / "u1_stale"
    stale.mkdir(parents=True)
    (stale / "media.mp4").write_bytes(b"x" * 4096)
    msg = make_message(user_id=ADMIN)
    await admin.cmd_cleanup(msg, _command(""))
    assert not stale.exists()
    assert "Cleanup done" in msg.edits[-1]
    assert "freed" in msg.edits[-1]


async def test_cleanup_empty(services, make_message):
    msg = make_message(user_id=ADMIN)
    await admin.cmd_cleanup(msg, _command(""))
    assert "Nothing to clean" in msg.edits[-1]


async def test_cleanup_respects_age_argument(services, make_message):
    import os
    import time

    temp_dir = admin.settings.temp_dir_resolved
    fresh = temp_dir / "u1_fresh"
    fresh.mkdir(parents=True)
    msg = make_message(user_id=ADMIN)
    await admin.cmd_cleanup(msg, _command("3600"))
    assert fresh.exists()

    old = temp_dir / "u1_old"
    old.mkdir(parents=True)
    old_time = time.time() - 7200
    os.utime(old, (old_time, old_time))
    msg = make_message(user_id=ADMIN)
    await admin.cmd_cleanup(msg, _command("3600"))
    assert not old.exists()


async def test_parse_cleanup_age():
    assert admin._parse_cleanup_age(None) == 0
    assert admin._parse_cleanup_age("") == 0
    assert admin._parse_cleanup_age("600") == 600
    assert admin._parse_cleanup_age(" 60 ") == 60
    assert admin._parse_cleanup_age("nope") == 0
    assert admin._parse_cleanup_age("-5") == 0


async def test_parse_user_id():
    assert admin._parse_user_id(" 123 ") == 123
    assert admin._parse_user_id("@alice") is None
    assert admin._parse_user_id("") is None
    assert admin._parse_user_id(None) is None
