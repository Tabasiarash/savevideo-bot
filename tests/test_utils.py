from __future__ import annotations

import pytest
from bot.utils import (
    URLBlockedError,
    URLParseError,
    check_url_safety,
    extract_urls,
    format_duration,
    format_size,
    hostname_from_url,
    ip_is_public,
    is_supported_scheme,
    normalize_url,
    sanitize_filename,
    site_from_url,
    target_bitrate_bps,
)

# --- extraction -----------------------------------------------------------


def test_extract_urls_multiple():
    text = "watch https://youtu.be/abc and https://x.com/user/status/1? s=20 ok"
    assert extract_urls(text) == ["https://youtu.be/abc", "https://x.com/user/status/1"]


def test_extract_urls_strips_trailing_punctuation():
    assert extract_urls("see (https://example.com/a).") == ["https://example.com/a"]
    assert extract_urls("link: https://example.com/x, and") == ["https://example.com/x"]


def test_extract_urls_balances_parentheses():
    url = "https://en.wikipedia.org/wiki/Foo_(bar)"
    assert extract_urls(f"<{url}>") == [url]


# --- normalisation --------------------------------------------------------


def test_normalize_expands_youtu_be():
    assert normalize_url("https://youtu.be/dQw4w9WgXcQ") == "https://youtube.com/watch?v=dQw4w9WgXcQ"


def test_normalize_strips_tracking_and_www():
    out = normalize_url("https://www.YouTube.com/watch?v=abc&utm_source=x&si=deadbeef")
    assert out == "https://youtube.com/watch?v=abc"


def test_normalize_lowercases_scheme_host_and_default_port():
    assert normalize_url("HTTPS://Example.com:443/Path") == "https://example.com/Path"


def test_normalize_rejects_non_http():
    with pytest.raises(URLParseError):
        normalize_url("ftp://example.com/file")
    with pytest.raises(URLParseError):
        normalize_url("javascript:alert(1)")


def test_is_supported_scheme():
    assert is_supported_scheme("https://a.com")
    assert is_supported_scheme("http://a.com")
    assert not is_supported_scheme("ftp://a.com")
    assert not is_supported_scheme("not a url")


def test_hostname_and_site():
    assert hostname_from_url("https://www.youtube.com/watch?v=1") == "youtube.com"
    assert site_from_url("https://m.youtube.com/watch?v=1") == "youtube"
    assert site_from_url("https://example.org/x") == "example.org"


# --- SSRF -----------------------------------------------------------------


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("192.168.1.1", False),
        ("172.16.0.1", False),
        ("169.254.169.254", False),
        ("0.0.0.0", False),
        ("::1", False),
        ("fd00::1", False),
        ("not-an-ip", False),
    ],
)
def test_ip_is_public(ip, expected):
    assert ip_is_public(ip) is expected


async def test_check_url_safety_blocks_private_literal():
    with pytest.raises(URLBlockedError):
        await check_url_safety("http://127.0.0.1:8080/anything")
    with pytest.raises(URLBlockedError):
        await check_url_safety("http://169.254.169.254/latest/meta-data/")


async def test_check_url_safety_blocks_localhost():
    with pytest.raises(URLBlockedError):
        await check_url_safety("http://localhost/admin")


async def test_check_url_safety_blocks_bad_scheme():
    with pytest.raises(URLBlockedError):
        await check_url_safety("file:///etc/passwd")


# --- formatting -----------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(0, "0 B"), (512, "512 B"), (1024, "1.0 KB"), (1536, "1.5 KB"), (None, "?"), (-1, "?")],
)
def test_format_size(value, expected):
    assert format_size(value) == expected


def test_format_size_mb():
    assert format_size(50 * 1024 * 1024) == "50.0 MB"


@pytest.mark.parametrize(
    "value,expected",
    [(None, "?"), (0, "?"), (5, "0:05"), (65, "1:05"), (3600, "1:00:00"), (3725, "1:02:05")],
)
def test_format_duration(value, expected):
    assert format_duration(value) == expected


def test_sanitize_filename():
    assert sanitize_filename("a/b:c*d?e") == "a_b_c_d_e"
    assert sanitize_filename("   ..trim..  ") == "trim"
    assert sanitize_filename("") == "download"
    assert len(sanitize_filename("x" * 500)) == 180


def test_target_bitrate_bps():
    assert target_bitrate_bps(0, 10) == 0
    assert target_bitrate_bps(1000, 0) == 0
    assert target_bitrate_bps(8_000_000, 8) == int(8_000_000 * 8 * 0.95 / 8)
