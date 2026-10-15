"""Embed title/artist/cover into a downloaded audio file with ffmpeg.

Only used by the Spotify metadata-match flow, where the tags come from Spotify
metadata rather than from the file's own container. ffmpeg (no new dependency)
rewrites the file in place: a cover is attached as an ID3 APIC frame for MP3 and
as an ``attached_pic`` stream for M4A/MP4. Text tags are written even when no
cover is available. Everything is best-effort: any failure leaves the original
file untouched and returns ``False`` so the caller can still upload it.

The command is always built as an argument list (never a shell), and tag values
are sanitised because they originate from untrusted pages.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

MP3_SUFFIXES = {".mp3"}
MP4_SUFFIXES = {".m4a", ".mp4", ".m4b"}
SUPPORTED_SUFFIXES = MP3_SUFFIXES | MP4_SUFFIXES

_ILLEGAL = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_tag(value: str | None, *, limit: int = 200) -> str:
    """Strip control characters/newlines and cap length for a metadata field."""
    if not value:
        return ""
    cleaned = _ILLEGAL.sub(" ", str(value))
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:limit]


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def _run_ffmpeg(args: list[str], timeout: int) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def build_tag_args(
    audio: Path,
    out: Path,
    *,
    title: str,
    artist: str,
    cover: Path | None,
) -> list[str]:
    """Assemble the ffmpeg argument list for tagging ``audio`` into ``out``."""
    suffix = audio.suffix.lower()
    args = ["-y", "-i", str(audio)]
    has_cover = cover is not None and cover.exists()
    if has_cover:
        args += ["-i", str(cover)]
        args += ["-map", "0:a", "-map", "1:v", "-c:v", "copy"]
        if suffix in MP4_SUFFIXES:
            args += ["-disposition:v", "attached_pic"]
    else:
        args += ["-map", "0:a"]
    args += ["-c:a", "copy"]
    if title:
        args += ["-metadata", f"title={title}"]
    if artist:
        args += ["-metadata", f"artist={artist}"]
    if has_cover and suffix in MP3_SUFFIXES:
        args += [
            "-id3v2_version",
            "3",
            "-metadata:s:v",
            "title=Album cover",
            "-metadata:s:v",
            "comment=Cover (front)",
        ]
    args.append(str(out))
    return args


async def tag_audio(
    path: Path,
    *,
    title: str | None = None,
    artist: str | None = None,
    cover: Path | None = None,
    timeout: int = 120,
    runner: Callable[[list[str], int], bool] | None = None,
) -> bool:
    """Tag ``path`` in place. Returns ``True`` only when ffmpeg succeeded."""
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False

    safe_title = sanitize_tag(title)
    safe_artist = sanitize_tag(artist)
    if not safe_title and not safe_artist and cover is None:
        return False

    out = path.with_name(f"{path.stem}.tagged{path.suffix}")
    args = build_tag_args(path, out, title=safe_title, artist=safe_artist, cover=cover)
    run = runner or _run_ffmpeg
    try:
        ok = run(args, timeout)
    except Exception:  # noqa: BLE001 - tagging must never break a download
        log.debug("ffmpeg tagging raised", exc_info=True)
        ok = False

    if not ok or not out.exists() or out.stat().st_size == 0:
        _remove(out)
        return False

    try:
        os.replace(out, path)
    except OSError:
        _remove(out)
        return False
    return True
