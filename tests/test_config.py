from __future__ import annotations

import pytest
from bot.config import Settings


def _settings(**kwargs) -> Settings:
    base = {"_env_file": None, "bot_token": "x"}
    base.update(kwargs)
    return Settings(**base)


def test_admin_ids_from_csv_string():
    s = _settings(admin_ids="1, 2 ,3, ")
    assert s.admin_ids == [1, 2, 3]


def test_admin_ids_from_list():
    s = _settings(admin_ids=[5, 6])
    assert s.admin_ids == [5, 6]


def test_admin_ids_blank():
    assert _settings(admin_ids="").admin_ids == []
    assert _settings(admin_ids="  ").admin_ids == []


def test_telegram_api_id_blank_is_zero():
    assert _settings(telegram_api_id="").telegram_api_id == 0
    assert _settings(telegram_api_id="12345").telegram_api_id == 12345


def test_derived_helpers():
    s = _settings(max_mb=50, max_duration_min=60, cache_ttl_days=30, cooldown_seconds=3)
    assert s.upload_limit_bytes == 50 * 1024 * 1024
    assert s.max_duration_s == 3600
    assert s.cache_ttl_s == 30 * 24 * 3600


def test_is_admin():
    s = _settings(admin_ids=[7, 8])
    assert s.is_admin(7)
    assert not s.is_admin(9)


def test_installs_local_api_flag():
    assert _settings().installs_local_api is False
    assert _settings(local_api=True).installs_local_api is True
    assert _settings(telegram_api_id=1).installs_local_api is True


def test_blank_token_allowed_but_empty(monkeypatch):
    s = _settings()
    assert s.bot_token == "x"


def test_defaults_are_graceful():
    s = _settings()
    assert s.max_jobs == 2
    assert s.per_user_daily_limit == 30
    assert s.default_language == "en"


@pytest.mark.parametrize("bad", ["notanint", "1,abc"])
def test_admin_ids_invalid_raises(bad):
    with pytest.raises(ValueError):
        _settings(admin_ids=bad)
