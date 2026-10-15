"""Minimal async HTTP GET used by the Spotify metadata providers.

Blocking ``curl_cffi`` runs in a thread so a SOCKS/HTTP ``PROXY_URL_SPOTIFY`` is
honoured (curl_cffi supports both; aiohttp would need an extra dependency for
SOCKS).

Safety properties, because the fetched URLs are attacker-adjacent (Spotify page
metadata can nominate an arbitrary cover URL):

* responses are read **streaming** and truncated to ``max_bytes`` on the wire, so
  a hostile/huge body cannot exhaust memory;
* every request has a timeout, and a caller-supplied ``is_allowed`` predicate is
  checked on **each redirect hop** — a redirect off the allow-list is never
  followed (the 3xx response is returned to the caller instead);
* tests monkeypatch :func:`fetch`, so no network is required in the suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    text: str
    url: str
    content: bytes = b""


def _header(headers: dict[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def _read_capped(resp, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` from a streaming response (never more)."""
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content():
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    return b"".join(chunks)[:max_bytes]


def _close(resp) -> None:
    with contextlib.suppress(Exception):
        resp.close()


def _make_response(resp, content: bytes, url: str) -> HttpResponse:
    headers = {k.lower(): v for k, v in dict(resp.headers).items()}
    return HttpResponse(
        status=int(resp.status_code),
        headers=headers,
        text=content.decode("utf-8", "replace"),
        url=url,
        content=content,
    )


def _fetch_blocking(
    url: str,
    *,
    proxy: str | None,
    timeout: float,
    max_bytes: int,
    allow_redirects: bool,
    headers: dict[str, str] | None,
    is_allowed: Callable[[str], bool] | None,
    max_redirects: int,
) -> HttpResponse:
    import curl_cffi.requests as requests

    proxies = {"http": proxy, "https": proxy} if proxy else None
    merged = {"User-Agent": "Mozilla/5.0 (compatible; savevideo-bot/1.0)"}
    if headers:
        merged.update(headers)

    # Follow redirects ourselves only when a host predicate is provided, so we
    # can vet every hop. Otherwise defer to curl_cffi's redirect handling.
    manual = allow_redirects and is_allowed is not None
    auto = allow_redirects and not manual

    deadline = time.monotonic() + timeout
    current = url
    for _ in range(max(1, max_redirects) + 1):
        remaining = max(0.1, deadline - time.monotonic())
        resp = requests.get(
            current,
            proxies=proxies,
            timeout=remaining,
            allow_redirects=auto,
            headers=merged,
            stream=True,
        )
        head = {k.lower(): v for k, v in dict(resp.headers).items()}
        location = _header(head, "location")
        if manual and int(resp.status_code) in _REDIRECT_STATUSES and location:
            next_url = urljoin(current, location)
            if not is_allowed(next_url):
                _close(resp)
                return _make_response(resp, b"", current)
            current = next_url
            _close(resp)
            continue
        try:
            content = _read_capped(resp, max_bytes)
        finally:
            _close(resp)
        return _make_response(resp, content, str(current if manual else resp.url))

    return _make_response(resp, b"", current)


async def fetch(
    url: str,
    *,
    proxy: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_redirects: bool = False,
    headers: dict[str, str] | None = None,
    is_allowed: Callable[[str], bool] | None = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> HttpResponse:
    """GET ``url`` off the event loop, with an optional per-redirect host check."""
    return await asyncio.wait_for(
        asyncio.to_thread(
            _fetch_blocking,
            url,
            proxy=proxy,
            timeout=timeout,
            max_bytes=max_bytes,
            allow_redirects=allow_redirects,
            headers=headers,
            is_allowed=is_allowed,
            max_redirects=max_redirects,
        ),
        timeout=timeout + 2,
    )


def _post_form_blocking(
    url: str,
    data: dict[str, str],
    *,
    proxy: str | None,
    timeout: float,
    max_bytes: int,
) -> HttpResponse:
    import curl_cffi.requests as requests

    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.post(url, data=data, proxies=proxies, timeout=timeout, stream=True)
    try:
        content = _read_capped(resp, max_bytes)
    finally:
        _close(resp)
    return _make_response(resp, content, str(resp.url))


async def post_form(
    url: str,
    data: dict[str, str],
    *,
    proxy: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> HttpResponse:
    """POST an ``application/x-www-form-urlencoded`` body off the event loop."""
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_form_blocking,
            url,
            data,
            proxy=proxy,
            timeout=timeout,
            max_bytes=max_bytes,
        ),
        timeout=timeout + 2,
    )
