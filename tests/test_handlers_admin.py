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
    for expected in ("Proxy: off", "Cookies: none", "JS runtime: deno", "curl_cffi: 0.13.0", "Egress: ✅"):
        assert expected in body


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


async def test_parse_user_id():
    assert admin._parse_user_id(" 123 ") == 123
    assert admin._parse_user_id("@alice") is None
    assert admin._parse_user_id("") is None
    assert admin._parse_user_id(None) is None
