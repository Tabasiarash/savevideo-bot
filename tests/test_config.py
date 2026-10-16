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


def test_max_upload_mb_default_is_cloud_50():
    s = _settings()
    assert s.max_upload_mb == 50
    assert s.is_local_api is False


def test_bot_api_url_auto_raises_limit_to_2000():
    s = _settings(bot_api_url="http://127.0.0.1:8081")
    assert s.is_local_api is True
    assert s.max_upload_mb == 2000


def test_explicit_max_upload_mb_wins_over_auto():
    s = _settings(bot_api_url="http://127.0.0.1:8081", max_upload_mb=500)
    assert s.max_upload_mb == 500


def test_max_upload_mb_clamped_to_ceiling():
    assert _settings(max_upload_mb=99999).max_upload_mb == 2000


def test_legacy_max_mb_alias_still_works():
    assert _settings(max_mb=75).max_upload_mb == 75


def test_legacy_local_api_derives_bot_api_url():
    s = _settings(local_api=True, local_api_base="http://telegram-bot-api:8081")
    assert s.is_local_api is True
    assert s.bot_api_url == "http://telegram-bot-api:8081"
    assert s.max_upload_mb == 2000


def test_upload_timeout_default():
    assert _settings().upload_timeout_seconds == 3600


def test_fits_upload_limit_method():
    s = _settings(max_mb=50)
    assert s.fits_upload_limit(49 * 1024 * 1024) is True
    assert s.fits_upload_limit(50 * 1024 * 1024) is False


def test_js_runtime_env_aliases(monkeypatch):
    monkeypatch.delenv("JS_RUNTIME", raising=False)
    monkeypatch.setenv("YTDLP_JS_RUNTIME", "deno")
    assert Settings(_env_file=None, bot_token="x").js_runtime == "deno"


def test_max_upload_mb_env_alias(monkeypatch):
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    monkeypatch.setenv("MAX_MB", "123")
    assert Settings(_env_file=None, bot_token="x").max_upload_mb == 123


def test_blank_token_allowed_but_empty(monkeypatch):
    s = _settings()
    assert s.bot_token == "x"


def test_defaults_are_graceful():
    s = _settings()
    assert s.max_jobs == 2
    assert s.per_user_daily_limit == 30
    assert s.default_language == "en"


def test_anti_blocking_defaults():
    s = _settings()
    assert s.proxy_url == ""
    assert s.proxy_url_instagram == ""
    assert s.proxy_url_tiktok == ""
    assert s.proxy_url_x == ""
    assert s.cookies_file == ""
    assert s.ytdlp_extra_args == ""
    assert s.force_ipv4 is False
    assert s.force_ipv6 is False
    assert s.js_runtime == "auto"
    assert s.egress_test_url.startswith("https://")


def test_platform_proxy_env_aliases(monkeypatch):
    monkeypatch.setenv("PROXY_URL_TIKTOK", "socks5h://tt:1080")
    monkeypatch.setenv("PROXY_URL_X", "socks5h://x:1080")
    monkeypatch.setenv("PROXY_URL_INSTAGRAM", "socks5h://ig:1080")
    s = Settings(_env_file=None, bot_token="x")
    assert s.proxy_url_tiktok == "socks5h://tt:1080"
    assert s.proxy_url_x == "socks5h://x:1080"
    assert s.proxy_url_instagram == "socks5h://ig:1080"


def test_anti_blocking_overrides():
    s = _settings(
        force_ipv4=True,
        force_ipv6=True,
        js_runtime="deno",
        ytdlp_extra_args="--extractor-args youtube:player_client=web",
        proxy_url="socks5h://127.0.0.1:1080",
    )
    assert s.force_ipv4 is True
    assert s.js_runtime == "deno"
    assert "player_client" in s.ytdlp_extra_args


@pytest.mark.parametrize("bad", ["notanint", "1,abc"])
def test_admin_ids_invalid_raises(bad):
    with pytest.raises(ValueError):
        _settings(admin_ids=bad)


def test_spotify_defaults_are_off():
    s = _settings()
    assert s.enable_spotify is False
    assert s.spotify_match_sources == "youtube,soundcloud"
    assert s.spotify_sources() == ["youtube", "soundcloud"]
    assert s.spotify_enable_collections is False
    assert s.spotify_max_items == 25
    assert s.spotify_duration_tolerance_s == 4
    assert s.spotify_client_id == ""
    assert s.spotify_client_secret == ""


def test_spotify_sources_normalised():
    s = _settings(spotify_match_sources=" YouTube , soundcloud ,, youtube ")
    assert s.spotify_sources() == ["youtube", "soundcloud"]


def test_proxy_for_spotify_falls_back():
    assert _settings(proxy_url="socks5h://a:1").proxy_for_spotify() == "socks5h://a:1"
    assert (
        _settings(proxy_url="socks5h://a:1", proxy_url_spotify="socks5h://b:2").proxy_for_spotify()
        == "socks5h://b:2"
    )


def test_spotify_env_aliases(monkeypatch):
    monkeypatch.setenv("ENABLE_SPOTIFY", "true")
    monkeypatch.setenv("SPOTIFY_MAX_ITEMS", "7")
    monkeypatch.setenv("PROXY_URL_SPOTIFY", "socks5h://sp:1080")
    s = Settings(_env_file=None, bot_token="x")
    assert s.enable_spotify is True
    assert s.spotify_max_items == 7
    assert s.proxy_url_spotify == "socks5h://sp:1080"



def test_podcasts_disabled_by_default():
    s = _settings()
    assert s.enable_podcasts is False
    assert s.podcast_max_enclosure_bytes == 1024 * 1024 * 1024


def test_podcast_regions_list_parsing():
    assert _settings(podcast_regions="us,de,gb").podcast_regions_list() == ["us", "de", "gb"]
    assert _settings(podcast_regions="US, de ,").podcast_regions_list() == ["us", "de"]
    assert _settings(podcast_regions="bogus").podcast_regions_list() == ["us"]


def test_proxy_for_podcast_falls_back():
    assert _settings(proxy_url="socks5h://a:1").proxy_for_podcast() == "socks5h://a:1"
    assert (
        _settings(proxy_url="socks5h://a:1", proxy_url_podcast="socks5h://c:3").proxy_for_podcast()
        == "socks5h://c:3"
    )


def test_podcast_env_aliases(monkeypatch):
    monkeypatch.setenv("ENABLE_PODCASTS", "true")
    monkeypatch.setenv("PODCAST_MAX_DURATION_MIN", "90")
    s = Settings(_env_file=None, bot_token="x")
    assert s.enable_podcasts is True
    assert s.podcast_max_duration_min == 90
