"""Minimal async HTTP GET used by the Spotify metadata providers.

Blocking ``curl_cffi`` runs in a thread so a SOCKS/HTTP ``PROXY_URL_SPOTIFY`` is
honoured (curl_cffi supports both; aiohttp would need an extra dependency for
SOCKS). Responses are size-capped and never streamed to disk. Tests monkeypatch
:func:`fetch`, so no network is required in the suite.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    text: str
    url: str


def _fetch_blocking(
    url: str,
    *,
    proxy: str | None,
    timeout: float,
    max_bytes: int,
    allow_redirects: bool,
    headers: dict[str, str] | None,
) -> HttpResponse:
    import curl_cffi.requests as requests

    proxies = {"http": proxy, "https": proxy} if proxy else None
    merged = {"User-Agent": "Mozilla/5.0 (compatible; savevideo-bot/1.0)"}
    if headers:
        merged.update(headers)
    resp = requests.get(
        url,
        proxies=proxies,
        timeout=timeout,
        allow_redirects=allow_redirects,
        headers=merged,
    )
    content = resp.content or b""
    if len(content) > max_bytes:
        content = content[:max_bytes]
    return HttpResponse(
        status=int(resp.status_code),
        headers={k.lower(): v for k, v in dict(resp.headers).items()},
        text=content.decode("utf-8", "replace"),
        url=str(resp.url),
    )


async def fetch(
    url: str,
    *,
    proxy: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_redirects: bool = False,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """GET ``url`` and return status/headers/body (truncated) off the event loop."""
    return await asyncio.wait_for(
        asyncio.to_thread(
            _fetch_blocking,
            url,
            proxy=proxy,
            timeout=timeout,
            max_bytes=max_bytes,
            allow_redirects=allow_redirects,
            headers=headers,
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
    resp = requests.post(url, data=data, proxies=proxies, timeout=timeout)
    content = resp.content or b""
    if len(content) > max_bytes:
        content = content[:max_bytes]
    return HttpResponse(
        status=int(resp.status_code),
        headers={k.lower(): v for k, v in dict(resp.headers).items()},
        text=content.decode("utf-8", "replace"),
        url=str(resp.url),
    )


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

