#!/usr/bin/env python3
"""Export fresh YouTube login cookies from a local Chrome/Chromium profile.

YouTube's "Sign in to confirm you're not a bot" wall on datacenter IPs is beaten
by (a) fresh cookies from a logged-in account, (b) a running bgutil PO-token
provider, and (c) a JS runtime. This script produces the cookies.txt for (a) by
decrypting the browser's cookie store directly, which is handy on a headless box
where `yt-dlp --cookies-from-browser` cannot unlock the OS keyring.

Usage:
    sudo DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus \
        python3 scripts/refresh_cookies.py \
        --profile "$HOME/.config/google-chrome/Default" \
        --output /etc/savevideo-bot/yt_cookies.txt

Linux only. Needs `secret-tool` (libsecret-tools) plus either pycryptodome
(`pip install pycryptodome`) or the `cryptography` package.

NOTE: these are full account cookies. Treat the output as a secret (chmod 600),
and be aware that automating a Google account can get it rate-limited/blocked.
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from hashlib import pbkdf2_hmac, sha256
from pathlib import Path


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    try:
        from Cryptodome.Cipher import AES  # type: ignore

        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except ImportError:
        pass
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return dec.update(data) + dec.finalize()
    except ImportError as exc:  # pragma: no cover - depends on host
        raise SystemExit(
            "need one of: pip install pycryptodome  |  pip install cryptography"
        ) from exc


def _strip_pkcs7(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


def _safe_storage_key(service: str) -> bytes:
    """Derive Chrome's AES key from the OS keyring ('Chrome Safe Storage')."""
    try:
        password = subprocess.check_output(
            ["secret-tool", "lookup", "application", service], stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            f"could not read '{service}' from the keyring (is DBUS/secret-tool set up?)"
        ) from exc
    if not password:
        raise SystemExit(f"keyring entry '{service}' is empty")
    return pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)


def _decrypt_value(enc: bytes, key: bytes, host_key: str) -> str:
    if not enc:
        return ""
    if not enc.startswith(b"v1"):  # v10/v11 are AES-128-CBC with IV of spaces
        return enc.decode("utf-8", "replace")
    plain = _strip_pkcs7(_aes_cbc_decrypt(key, b" " * 16, enc[3:]))
    # Chrome >=130 prefixes the value with SHA256(host_key); drop it when present.
    if len(plain) > 32 and plain[:32] == sha256(host_key.encode()).digest():
        plain = plain[32:]
    return plain.decode("utf-8", "replace")


def export_cookies(db_path: Path, out_path: Path, service: str) -> int:
    key = _safe_storage_key(service)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute(
        "select host_key, name, value, encrypted_value, path, is_secure, "
        "expires_utc from cookies"
    ).fetchall()
    con.close()

    now = int(time.time())
    lines = ["# Netscape HTTP Cookie File", "# Chrome-derived; keep secret.", ""]
    written = 0
    for host, name, value, enc, path, secure, expires_utc in rows:
        val = value or _decrypt_value(enc, key, host)
        if not val:
            continue
        expires = int(expires_utc / 1_000_000 - 11644473600) if expires_utc else 0
        if expires and expires < now:
            continue
        lines.append(
            "\t".join(
                [
                    host,
                    "TRUE" if host.startswith(".") else "FALSE",
                    path or "/",
                    "TRUE" if secure else "FALSE",
                    str(expires),
                    name,
                    val,
                ]
            )
        )
        written += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_path.chmod(0o600)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path.home() / ".config/google-chrome/Default",
        help="Chrome/Chromium profile directory (contains 'Cookies')",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("./cookies.txt"), help="cookies.txt to write"
    )
    parser.add_argument(
        "--keyring-service",
        default="chrome",
        help="keyring entry name (e.g. 'chrome', 'chromium')",
    )
    args = parser.parse_args()

    db = args.profile_dir / "Cookies"
    if not db.exists():
        raise SystemExit(f"no cookie database at {db}")
    count = export_cookies(db, args.output, args.keyring_service)
    print(f"wrote {count} cookies -> {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
