from __future__ import annotations

from pathlib import Path

import pytest
from bot.handlers import links
from bot.services.downloader import InfoError
from bot.utils import URLBlockedError

USER = 42


async def _open_flow(services, msg, runner, url="https://www.youtube.com/watch?v=abc123", playlist=False):
    runner.info["webpage_url"] = url
    await links.handle_urls(msg, [url], playlist=playlist)
    return links._pending[USER]


# --- entry -----------------------------------------------------------------


async def test_on_text_message_unknown_command(services, make_message):
    msg = make_message(text="/nope")
    await links.on_text_message(msg)
    assert "Unknown command" in msg.sent[0]


async def test_on_text_message_no_url(services, make_message):
    msg = make_message(text="hello there")
    await links.on_text_message(msg)
    assert "Unknown command" in msg.sent[0]


async def test_on_text_message_extracts_and_handles(services, runner, make_message):
    msg = make_message(text="check this https://www.youtube.com/watch?v=abc123")
    await links.on_text_message(msg)
    assert USER in links._pending
    assert runner.info_calls


# --- validation ------------------------------------------------------------


async def test_handle_urls_rejects_non_http(services, make_message):
    msg = make_message()
    await links.handle_urls(msg, ["ftp://example.com/x"], playlist=False)
    assert "http" in msg.sent[0].lower()


async def test_handle_urls_ssrf_blocked(services, make_message, monkeypatch):
    msg = make_message()

    async def blocked(url):
        raise URLBlockedError("nope")

    monkeypatch.setattr(links, "check_url_safety", blocked)
    await links.handle_urls(msg, ["https://evil.example/x"], playlist=False)
    assert msg.sent and "private" in msg.sent[0].lower()


async def test_handle_urls_banned(services, make_message):
    await services.db.upsert_user(USER, "u", 0)
    await services.db.set_banned(USER, True)
    msg = make_message()
    await links.handle_urls(msg, ["https://www.youtube.com/watch?v=abc123"], playlist=False)
    assert "not allowed" in msg.sent[0].lower()


async def test_handle_urls_daily_limit(services, make_message, settings, monkeypatch):
    monkeypatch.setattr(settings, "per_user_daily_limit", 0, raising=False)
    msg = make_message()
    await links.handle_urls(msg, ["https://www.youtube.com/watch?v=abc123"], playlist=False)
    assert "daily" in msg.sent[0].lower()


async def test_handle_urls_duration_too_long(services, runner, make_message):
    runner.info["duration"] = 999999
    msg = make_message()
    await links.handle_urls(msg, ["https://www.youtube.com/watch?v=abc123"], playlist=False)
    assert "exceeds" in " ".join(msg.edits)
    assert await services.db.downloads_total() == 1
    assert USER not in links._pending


async def test_handle_urls_info_error(services, runner, make_message):
    runner.info_error = InfoError("extractor exploded")
    msg = make_message()
    await links.handle_urls(msg, ["https://www.youtube.com/watch?v=abc123"], playlist=False)
    assert any("Could not fetch info" in e for e in msg.edits)
    assert await services.db.downloads_total() == 1


async def test_handle_urls_signin_hint(services, runner, make_message):
    runner.info_error = InfoError("Sign in to confirm you're not a bot")
    msg = make_message()
    await links.handle_urls(msg, ["https://www.youtube.com/watch?v=abc123"], playlist=False)
    assert any("confirm you're not a bot" in e for e in msg.edits)


# --- format picker ---------------------------------------------------------


async def test_happy_path_shows_keyboard(services, runner, make_message):
    msg = make_message()
    flow = await _open_flow(services, msg, runner)
    assert flow.prefix == "dl"
    assert any("Fetching" in s for s in msg.sent)
    assert any("Test Video" in e for e in msg.edits)
    markup = msg.markups[-1]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "dl:video_720" in data
    assert "dl:video_best" in data
    assert "dl:audio_mp3" in data
    assert "dl:audio_m4a" in data
    assert "dl:thumbnail" in data
    assert "dl:cancel" in data


async def test_playlist_uses_pl_prefix(services, runner, make_message):
    runner.info["entries"] = [{"title": "a"}, {"title": "b"}]
    msg = make_message()
    await links.handle_urls(msg, ["https://www.youtube.com/playlist?list=x"], playlist=True)
    flow = links._pending[USER]
    assert flow.prefix == "pl"
    data = [b.callback_data for row in msg.markups[-1].inline_keyboard for b in row]
    assert all(d.startswith("pl:") for d in data)


async def test_on_format_pick_expired(services, make_callback):
    cb = make_callback(data="dl:video_720")
    await links.on_format_pick(cb, services.bot)
    assert cb.answers[0][1] is True
    assert cb.answers[0][0]


async def test_on_format_pick_cancel(services, runner, make_message, make_callback):
    msg = make_message()
    await _open_flow(services, msg, runner)
    cb = make_callback(data="dl:cancel", message=msg)
    await links.on_format_pick(cb, services.bot)
    assert USER not in links._pending
    assert msg.deleted is True


# --- downloads -------------------------------------------------------------


async def test_download_video_happy_path(services, runner, bot, make_message, make_callback):
    msg = make_message()
    flow = await _open_flow(services, msg, runner)
    cb = make_callback(data="dl:video_720", message=msg)
    await links.on_format_pick(cb, bot)

    assert len(bot.videos) == 1
    chat_id, _, kwargs = bot.videos[0]
    assert chat_id == USER
    assert kwargs["height"] == 720
    assert USER not in links._pending
    entry = await services.cache.get(flow.url_hash, "video_720")
    assert entry is not None and entry.file_id == "vid1"
    assert await services.db.downloads_total() == 1


async def test_download_audio_happy_path(services, runner, bot, make_message, make_callback):
    msg = make_message()
    flow = await _open_flow(services, msg, runner)
    cb = make_callback(data="dl:audio_mp3", message=msg)
    await links.on_format_pick(cb, bot)

    assert len(bot.audios) == 1
    entry = await services.cache.get(flow.url_hash, "audio_mp3")
    assert entry is not None
    assert len(bot.videos) == 0


async def test_thumbnail_flow(services, runner, bot, make_message, make_callback):
    msg = make_message()
    await _open_flow(services, msg, runner)
    cb = make_callback(data="dl:thumbnail", message=msg)
    await links.on_format_pick(cb, bot)
    assert len(bot.photos) == 1


async def test_download_error_is_shown(services, runner, bot, make_message, make_callback):
    from bot.services.downloader import DownloadError

    runner.download_error = DownloadError("boom")
    msg = make_message()
    await _open_flow(services, msg, runner)
    cb = make_callback(data="dl:video_720", message=msg)
    await links.on_format_pick(cb, bot)
    assert any("Download failed" in e for e in msg.edits)
    assert await services.db.downloads_total() == 1


async def test_upload_failure_recorded(services, runner, bot, make_message, make_callback):
    msg = make_message()
    await _open_flow(services, msg, runner)
    bot.raise_for.add(USER)
    cb = make_callback(data="dl:video_720", message=msg)
    await links.on_format_pick(cb, bot)
    assert any("Upload failed" in e for e in msg.edits)


async def test_size_fallback_to_lower_height_then_reencode(
    services, runner, make_message, monkeypatch
):
    msg = make_message()
    flow = await _open_flow(services, msg, runner)
    flow.video_heights = [720, 480]
    runner.media_size = 5 * 1024 * 1024
    limit = 1024 * 1024
    big = flow.tmpdir / "media.mp4"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"\x00" * runner.media_size)

    async def fake_transcode(input_path, outdir, limit_bytes, duration_s, on_progress=None):
        out = Path(outdir) / "fitsize.mp4"
        out.write_bytes(b"\x00" * 100)
        return out

    monkeypatch.setattr(links, "transcode_to_size", fake_transcode)
    result = await links._fit_video_size(services, flow, "video_720", [big], runner.media_size, limit)
    assert result is not None
    assert result[0].name == "fitsize.mp4"
    assert [c.format_key for c in runner.calls if hasattr(c, "format_key")] == ["video_480"]


async def test_size_fallback_impossible_gives_error(services, runner, make_message, monkeypatch):
    msg = make_message()
    flow = await _open_flow(services, msg, runner)
    flow.video_heights = [720]
    big = flow.tmpdir / "media.mp4"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"\x00" * 5_000_000)

    async def failing_transcode(*args, **kwargs):
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(links, "transcode_to_size", failing_transcode)
    result = await links._fit_video_size(services, flow, "video_720", [big], 5_000_000, 1024)
    assert result is None
    assert any("cannot be made to fit" in e for e in msg.edits)


async def test_audio_oversize_without_split_gives_error(services, runner, make_message, monkeypatch):
    msg = make_message()
    flow = await _open_flow(services, msg, runner)
    big = flow.tmpdir / "media.mp3"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"\x00" * 5_000_000)

    async def failing_audio_transcode(*args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(links, "transcode_audio_to_size", failing_audio_transcode)
    result = await links._fit_audio_size(services, flow, [big], 5_000_000, 1024)
    assert result is None
    assert any("cannot be made to fit" in e for e in msg.edits)


async def test_multi_part_audio_upload(services, runner, bot, make_message, make_callback, monkeypatch):
    msg = make_message()
    await _open_flow(services, msg, runner)
    runner.media_size = 5 * 1024 * 1024

    async def fake_transcode_audio(input_path, outdir, limit_bytes, duration_s, on_progress=None):
        out = Path(outdir) / "small.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 100)
        return out

    monkeypatch.setattr(links, "transcode_audio_to_size", fake_transcode_audio)
    cb = make_callback(data="dl:audio_mp3", message=msg)
    await links.on_format_pick(cb, bot)
    assert len(bot.audios) == 1


async def test_gallery_download(services, runner, bot, make_message, make_callback):
    msg = make_message()
    runner.info = {
        "title": "Post",
        "duration": 0,
        "extractor_key": "Instagram",
        "entries": [
            {"url": "https://instagram.com/p/1", "title": "a"},
            {"url": "https://instagram.com/p/2", "title": "b"},
        ],
        "formats": [],
    }
    await links.handle_urls(msg, ["https://www.instagram.com/p/1"], playlist=False)
    flow = links._pending[USER]
    assert links._is_gallery(flow)
    cb = make_callback(data="dl:video_best", message=msg)
    await links.on_format_pick(cb, bot)
    assert len(bot.media_groups) == 1
    assert await services.db.downloads_total() == 1


# --- deeplinks and extractor retry ----------------------------------------


async def test_deeplink_expired(services, bot, make_message):
    msg = make_message()
    await links.handle_deeplink(msg, bot, "missing-token")
    assert any("expired" in s.lower() for s in msg.sent)


async def test_deeplink_valid_starts_flow(services, runner, bot, make_message):
    await services.db.deeplink_create("tok", "https://www.youtube.com/watch?v=abc123")
    msg = make_message()
    await links.handle_deeplink(msg, bot, "tok")
    assert USER in links._pending
    assert await services.db.deeplink_get("tok") is None


async def test_extract_retries_after_ytdlp_update(services, runner, monkeypatch, tmp_path):
    calls = {"n": 0}

    async def fake_extract(url, *, playlist=False, playlist_items=None, cookiejar_dir=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise InfoError("extractor broke")
        return runner.info

    monkeypatch.setattr(runner, "extract_info", fake_extract)
    services.updater.retry = True
    result = await links._extract(services, "https://youtube.com/watch?v=x", False, tmp_path)
    assert result == runner.info
    assert calls["n"] == 2
    assert services.updater.updates == 1


def test_next_lower_height():
    class Flow:
        video_heights = [1080, 720, 480]

    assert links._next_lower_height(Flow, 1080) == 720
    assert links._next_lower_height(Flow, 480) is None
    assert links._next_lower_height(Flow, None) == 1080
    assert links._next_lower_height(Flow, 123) == 1080


@pytest.mark.parametrize("fmt,expected", [("video_720", 720), ("video_best", None), ("audio_mp3", None)])
def test_height_of(fmt, expected):
    assert links._height_of(fmt) == expected
