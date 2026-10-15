from __future__ import annotations

from bot import main as main_mod


def test_build_bot_uses_cloud_api_by_default(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "bot_api_url", "")
    bot = main_mod.build_bot()
    try:
        assert bot.session.api.is_local is False
    finally:
        assert bot.session is not None


def test_build_bot_uses_local_api_with_timeout(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "bot_api_url", "http://127.0.0.1:8081")
    monkeypatch.setattr(main_mod.settings, "upload_timeout_seconds", 1234)
    bot = main_mod.build_bot()
    assert bot.session.api.is_local is True
    assert bot.session.api.base.startswith("http://127.0.0.1:8081")
    assert bot.session.timeout == 1234


async def test_set_commands_public_and_per_admin(monkeypatch):
    calls: list[tuple[list, object]] = []

    class FakeBot:
        async def set_my_commands(self, commands, scope=None):
            calls.append((commands, scope))

    monkeypatch.setattr(main_mod.settings, "admin_ids", [111, 222])
    await main_mod._set_commands(FakeBot())

    assert len(calls) == 3  # default plus one per admin
    default_cmds, default_scope = calls[0]
    assert default_scope is None
    assert [c.command for c in default_cmds] == ["start", "help", "language"]

    admin_cmds, admin_scope = calls[1]
    names = [c.command for c in admin_cmds]
    assert names[:3] == ["start", "help", "language"]
    for cmd in ("stats", "users", "broadcast", "ban", "unban", "update_ytdlp", "health"):
        assert cmd in names
    assert admin_scope.chat_id == 111
    assert calls[2][1].chat_id == 222


async def test_set_commands_tolerates_scope_failure(monkeypatch):
    class FakeBot:
        async def set_my_commands(self, commands, scope=None):
            if scope is not None:
                raise RuntimeError("chat not found")

    monkeypatch.setattr(main_mod.settings, "admin_ids", [999])
    await main_mod._set_commands(FakeBot())  # must not raise
