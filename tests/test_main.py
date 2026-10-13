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
