from __future__ import annotations

import asyncio
import contextlib

import pytest
from bot.services.podcast import download as dl

# --------------------------------------------------------------------------
# Minimal HTTP/1.1 server (Connection: close) used to exercise the fetcher.
# --------------------------------------------------------------------------

# Cookie headers seen across requests, so a test can prove none are ever sent.
SEEN_COOKIES: list[str] = []


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, dict[str, str]]:
    line = await reader.readline()
    headers: dict[str, str] = {}
    while True:
        raw = await reader.readline()
        if raw in (b"\r\n", b"\n", b""):
            break
        name, _, value = raw.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    cookie = headers.get("cookie")
    if cookie:
        SEEN_COOKIES.append(cookie)
    try:
        return line.decode("latin-1").split(" ")[1], headers
    except IndexError:  # pragma: no cover - malformed request
        return "/", headers


def _write(writer: asyncio.StreamWriter, status: int, headers: dict[str, str], body: bytes = b"") -> None:
    reason = {200: "OK", 302: "Found", 404: "Not Found"}.get(status, "OK")
    lines = [f"HTTP/1.1 {status} {reason}"]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    lines.append("Connection: close")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    path, _headers = await _read_request(reader)
    try:
        if path == "/ok.mp3" or path == "/cookie.mp3":
            _write(writer, 200, {"Content-Type": "audio/mpeg"}, b"AUDIO")
        elif path.startswith("/chain/"):
            hops = int(path.rsplit("/", 1)[1])
            if hops <= 0:
                _write(writer, 200, {"Content-Type": "audio/mpeg"}, b"AUDIO")
            else:
                _write(writer, 302, {"Location": f"/chain/{hops - 1}"})
        elif path == "/html":
            _write(writer, 200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>nope</html>")
        elif path == "/declare-big":
            # Declares a body larger than the cap up front; the fetcher must
            # abort on the header rather than stream it in.
            body = b"x" * 5000
            _write(writer, 200, {"Content-Type": "audio/mpeg", "Content-Length": str(len(body))}, body)
        elif path == "/stream-big":
            # No Content-Length: the fetcher must enforce the cap while streaming.
            _write(writer, 200, {"Content-Type": "audio/mpeg"}, b"x" * 5000)
        elif path == "/chunked-over":
            # Chunked transfer with total bytes exceeding cap (5000 bytes)
            _write(writer, 200, {"Content-Type": "audio/mpeg", "Transfer-Encoding": "chunked"})
            chunk = b"x" * 4000
            writer.write(f"{len(chunk):x}\r\n".encode("latin-1") + chunk + b"\r\n")
            writer.write(f"{len(chunk):x}\r\n".encode("latin-1") + chunk + b"\r\n")  # total 8000 > 5000 cap
            writer.write(b"0\r\n\r\n")
        elif path == "/chunked-ok":
            # Chunked transfer within cap
            _write(writer, 200, {"Content-Type": "audio/mpeg", "Transfer-Encoding": "chunked"})
            chunk = b"AUDIO"
            writer.write(f"{len(chunk):x}\r\n".encode("latin-1") + chunk + b"\r\n")
            writer.write(b"0\r\n\r\n")
        elif path == "/understate":
            # Content-Length lies low while the real (chunked) body is large;
            # the declared-size shortcut must not be trusted, the stream cap must.
            _write(writer, 200, {"Content-Type": "audio/mpeg", "Content-Length": "10", "Transfer-Encoding": "chunked"})
            chunk = b"Y" * 4000
            for _ in range(5):  # 20000 bytes total
                writer.write(f"{len(chunk):x}\r\n".encode("latin-1") + chunk + b"\r\n")
            writer.write(b"0\r\n\r\n")
        elif path == "/empty":
            _write(writer, 200, {"Content-Type": "audio/mpeg", "Content-Length": "0"})
        elif path == "/redir":
            _write(writer, 302, {"Location": "/ok.mp3"})
        elif path == "/redir-private":
            _write(writer, 302, {"Location": "http://169.254.169.254/latest/meta-data/"})
        elif path == "/loop":
            _write(writer, 302, {"Location": "/loop"})
        else:
            _write(writer, 404, {"Content-Type": "text/plain"}, b"no")
        await writer.drain()
    except (ConnectionError, OSError):  # client aborted (expected on cap tests)
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


@contextlib.asynccontextmanager
async def _server():
    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()


@pytest.fixture
def allow_local(monkeypatch):
    """Permit the loopback test address (SSRF checks are unit-tested below)."""
    monkeypatch.setattr(dl, "ip_is_public", lambda ip: ip in {"127.0.0.1"})


# --------------------------------------------------------------------------
# Policy checks that need no server
# --------------------------------------------------------------------------


async def test_blocks_loopback_literal(tmp_path):
    with pytest.raises(dl.EnclosureBlocked):
        await dl.stream_enclosure("http://127.0.0.1/a.mp3", tmp_path, max_bytes=1000)


async def test_blocks_metadata_literal(tmp_path):
    with pytest.raises(dl.EnclosureBlocked):
        await dl.stream_enclosure("http://169.254.169.254/a.mp3", tmp_path, max_bytes=1000)


async def test_rejects_bad_scheme(tmp_path):
    with pytest.raises(dl.EnclosureBlocked):
        await dl.stream_enclosure("ftp://example.com/a.mp3", tmp_path, max_bytes=1000)


# --------------------------------------------------------------------------
# End-to-end against the local server
# --------------------------------------------------------------------------


async def test_downloads_and_names_file(tmp_path, allow_local):
    async with _server() as port:
        path = await dl.stream_enclosure(f"http://127.0.0.1:{port}/ok.mp3", tmp_path, max_bytes=1000)
    assert path.exists()
    assert path.suffix == ".mp3"
    assert path.read_bytes() == b"AUDIO"


async def test_rejects_non_audio_content(tmp_path, allow_local):
    async with _server() as port:
        with pytest.raises(dl.EnclosureError):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/html", tmp_path, max_bytes=1000)


async def test_aborts_on_declared_oversize(tmp_path, allow_local):
    async with _server() as port:
        with pytest.raises(dl.EnclosureTooLarge):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/declare-big", tmp_path, max_bytes=1000)


async def test_enforces_cap_while_streaming(tmp_path, allow_local):
    async with _server() as port:
        with pytest.raises(dl.EnclosureTooLarge):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/stream-big", tmp_path, max_bytes=1000)
    assert list(tmp_path.iterdir()) == []


async def test_enforces_cap_for_chunked_response(tmp_path, allow_local):
    async with _server() as port:
        with pytest.raises(dl.EnclosureTooLarge):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/chunked-over", tmp_path, max_bytes=5000)
    assert list(tmp_path.iterdir()) == []


async def test_downloads_chunked_response(tmp_path, allow_local):
    async with _server() as port:
        path = await dl.stream_enclosure(f"http://127.0.0.1:{port}/chunked-ok", tmp_path, max_bytes=10000)
    assert path.read_bytes() == b"AUDIO"


async def test_enforces_cap_when_content_length_understated(tmp_path, allow_local):
    async with _server() as port:
        with pytest.raises(dl.EnclosureTooLarge):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/understate", tmp_path, max_bytes=5000)
    assert list(tmp_path.iterdir()) == []


async def test_rejects_empty_body(tmp_path, allow_local):
    async with _server() as port:
        with pytest.raises(dl.EnclosureError):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/empty", tmp_path, max_bytes=1000)


async def test_follows_redirect(tmp_path, allow_local):
    async with _server() as port:
        path = await dl.stream_enclosure(f"http://127.0.0.1:{port}/redir", tmp_path, max_bytes=1000)
    assert path.read_bytes() == b"AUDIO"


async def test_rejects_redirect_to_private(allow_local, tmp_path):
    # allow_local only whitelists 127.0.0.1, so the 169.254 hop is refused.
    async with _server() as port:
        with pytest.raises(dl.EnclosureBlocked):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/redir-private", tmp_path, max_bytes=1000)


async def test_rejects_redirect_loop(tmp_path, allow_local):
    async with _server() as port:
        with pytest.raises(dl.EnclosureError):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/loop", tmp_path, max_bytes=1000)


async def test_follows_redirect_chain_at_limit(tmp_path, allow_local):
    # Exactly five redirect hops then a body is still followed successfully.
    async with _server() as port:
        path = await dl.stream_enclosure(f"http://127.0.0.1:{port}/chain/5", tmp_path, max_bytes=1000)
    assert path.read_bytes() == b"AUDIO"


async def test_rejects_redirect_chain_over_limit(tmp_path, allow_local):
    # Six redirect hops exceeds the cap and must abort rather than keep going.
    async with _server() as port:
        with pytest.raises(dl.EnclosureError):
            await dl.stream_enclosure(f"http://127.0.0.1:{port}/chain/6", tmp_path, max_bytes=1000)


async def test_sends_no_cookies(tmp_path, allow_local):
    SEEN_COOKIES.clear()
    async with _server() as port:
        await dl.stream_enclosure(f"http://127.0.0.1:{port}/cookie.mp3", tmp_path, max_bytes=1000)
    assert SEEN_COOKIES == []


async def test_pins_connection_to_validated_address(tmp_path, allow_local, monkeypatch):
    # The connect must be pinned to the address we validated (via curl RESOLVE),
    # so a second DNS answer cannot move it (rebinding).
    seen: list[dict | None] = []
    real_session = dl.cffi.Session

    def spy(*args, **kwargs):
        seen.append(kwargs.get("curl_options"))
        return real_session(*args, **kwargs)

    monkeypatch.setattr(dl.cffi, "Session", spy)
    async with _server() as port:
        await dl.stream_enclosure(f"http://127.0.0.1:{port}/ok.mp3", tmp_path, max_bytes=1000)

    assert seen and seen[0] is not None
    assert seen[0][dl.CurlOpt.RESOLVE] == [f"127.0.0.1:{port}:127.0.0.1"]


def test_resolve_public_filters_private_answers(monkeypatch):
    monkeypatch.setattr(
        dl.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("10.0.0.5", 0)),
        ],
    )
    assert dl._resolve_public("mixed.example") == ["93.184.216.34"]


def test_resolve_public_rejects_all_private(monkeypatch):
    monkeypatch.setattr(
        dl.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))]
    )
    with pytest.raises(dl.EnclosureBlocked):
        dl._resolve_public("internal.example")
