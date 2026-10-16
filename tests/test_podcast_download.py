from __future__ import annotations

import asyncio
import contextlib

import pytest
from bot.services.podcast import download as dl

# --------------------------------------------------------------------------
# Minimal HTTP/1.1 server (Connection: close) used to exercise the fetcher.
# --------------------------------------------------------------------------


async def _read_path(reader: asyncio.StreamReader) -> str:
    line = await reader.readline()
    while True:
        header = await reader.readline()
        if header in (b"\r\n", b"\n", b""):
            break
    try:
        return line.decode("latin-1").split(" ")[1]
    except IndexError:  # pragma: no cover - malformed request
        return "/"


def _write(writer: asyncio.StreamWriter, status: int, headers: dict[str, str], body: bytes = b"") -> None:
    reason = {200: "OK", 302: "Found", 404: "Not Found"}.get(status, "OK")
    lines = [f"HTTP/1.1 {status} {reason}"]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    lines.append("Connection: close")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    path = await _read_path(reader)
    try:
        if path == "/ok.mp3":
            _write(writer, 200, {"Content-Type": "audio/mpeg"}, b"AUDIO")
        elif path == "/html":
            _write(writer, 200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>nope</html>")
        elif path == "/declare-big":
            # Declares a huge body up front; the fetcher must abort on the header.
            _write(writer, 200, {"Content-Type": "audio/mpeg", "Content-Length": "1000000000"})
        elif path == "/stream-big":
            # No Content-Length: the fetcher must enforce the cap while streaming.
            _write(writer, 200, {"Content-Type": "audio/mpeg"}, b"x" * 5000)
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
