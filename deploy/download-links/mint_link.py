#!/usr/bin/env python3
"""Mint a signed download link for testing nginx `secure_link`.

Kept standalone (stdlib only) so it runs without the bot's virtualenv. The
formula MUST stay byte-identical to secure_link_token() in
bot/services/download_links.py and to the secure_link_md5 directive:

    token = base64url( md5( f"{expires}{uri} {secret}" ) )   # '=' stripped

Usage:
    python3 mint_link.py https://dl.example.com /f/abc/media.mp4 SECRET [ttl_s]
"""

from __future__ import annotations

import base64
import hashlib
import sys
import time

USAGE = "usage: mint_link.py <base_url> <uri> <secret> [ttl_seconds]"


def secure_link_token(uri: str, expires: int, secret: str) -> str:
    digest = hashlib.md5(f"{expires}{uri} {secret}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def main(argv: list[str]) -> int:
    if len(sys.argv) < 4:
        print(USAGE, file=sys.stderr)
        return 2
    base_url, uri, secret = sys.argv[1], sys.argv[2], sys.argv[3]
    ttl = int(sys.argv[4]) if len(sys.argv) > 4 else 300
    expires = int(time.time()) + ttl
    token = secure_link_token(uri, expires, secret)
    print(f"{base_url.rstrip('/')}{uri}?md5={token}&expires={expires}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
