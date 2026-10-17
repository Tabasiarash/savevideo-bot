from __future__ import annotations

from types import SimpleNamespace

from bot.handlers import links, start

USER = 42


def _command(args: str = "") -> SimpleNamespace:
    return SimpleNamespace(args=args, command="start", prefix="/", mention=None)


async def test_cmd_start_greets(services, bot, make_message):
    msg = make_message(text="/start")
    await start.cmd_start(msg, _command(), bot)
    assert msg.sent and "Hi" in msg.sent[0]


async def test_cmd_start_banned(services, bot, make_message):
    await services.db.upsert_user(USER, "u", 0)
    await services.db.set_banned(USER, True)
    msg = make_message()
    await start.cmd_start(msg, _command(), bot)
    assert "not allowed" in msg.sent[0].lower()


async def test_cmd_start_lang_payload(services, bot, make_message):
    msg = make_message()
    await start.cmd_start(msg, _command("lang_de"), bot)
    assert await services.db.get_language(USER) == "de"


async def test_cmd_start_deeplink_payload(services, bot, make_message, monkeypatch):
    called = {}

    async def fake_deeplink(message, bot_, token):
        called["token"] = token

    monkeypatch.setattr(links, "handle_deeplink", fake_deeplink)
    msg = make_message()
    await start.cmd_start(msg, _command("dl_TOKENX"), bot)
    assert called["token"] == "TOKENX"


async def test_cmd_help(services, bot, make_message):
    msg = make_message()
    await start.cmd_help(msg, bot)
    assert "How to use me" in msg.sent[0]


async def test_cmd_language_keyboard(services, make_message):
    msg = make_message()
    await start.cmd_language(msg)
    data = [b.callback_data for row in msg.markups[-1].inline_keyboard for b in row]
    assert {"lang:en", "lang:de", "lang:fa"} == set(data)


async def test_on_language_pick_sets_language(services, make_callback):
    cb = make_callback(data="lang:fa")
    await start.on_language_pick(cb, services.bot)
    assert await services.db.get_language(42) == "fa"
    assert cb.answers


async def test_on_language_pick_unknown(services, make_callback):
    cb = make_callback(data="lang:zz")
    await start.on_language_pick(cb, services.bot)
    assert cb.answers == [("", False)]


async def test_cmd_settings_shows_modes(services, make_message, settings, monkeypatch):
    monkeypatch.setattr(settings, "enable_subtitles", True, raising=False)
    msg = make_message()
    await start.cmd_settings(msg)
    data = [b.callback_data for row in msg.markups[-1].inline_keyboard for b in row]
    assert {"setsub:burn", "setsub:embed", "setsub:srt"} == set(data)
    assert "Settings" in msg.sent[0]


async def test_cmd_settings_disabled(services, make_message):
    msg = make_message()
    await start.cmd_settings(msg)
    assert "Unknown command" in msg.sent[0]


async def test_on_subtitle_mode_pick_saves(services, make_callback):
    cb = make_callback(data="setsub:embed")
    await start.on_subtitle_mode_pick(cb)
    assert await services.db.get_subtitle_mode(USER) == "embed"
    assert cb.answers


async def test_on_subtitle_mode_pick_unknown(services, make_callback):
    cb = make_callback(data="setsub:zz")
    await start.on_subtitle_mode_pick(cb)
    assert cb.answers == [("", False)]
