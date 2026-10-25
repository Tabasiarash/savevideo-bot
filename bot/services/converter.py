"""Media converter service: video/audio/image quality reduction with ffmpeg.

Converts user-uploaded media files (video, audio, image) to a target format and
quality preset. ffmpeg is the only dependency (already required for subtitle
burning and audio tagging); no Pillow/PIL is added.

All functions are best-effort and never raise unexpected exceptions to callers;
failures return ``False`` so the handler can surface a friendly message.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ProgressCallback = Callable[[float], Any]

# Maximum dimension for image resizing in pixels (width OR height).
IMAGE_MAX_DIM = 1920

# Concurrency ceiling shared across conversion jobs so ffmpeg does not starve
# the host. The handler owns a semaphore instance and passes it in.
_MAX_CONCURRENT_DEFAULT = 3


@dataclass(frozen=True)
class MediaInfo:
    """Probed media metadata."""

    kind: str  # "video", "audio", "image", "unknown"
    mime: str | None = None
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    size_bytes: int | None = None
    vcodec: str | None = None
    acodec: str | None = None


@dataclass
class ConvertJob:
    """A single conversion request."""

    input_path: Path
    output_path: Path
    media_type: str  # "video", "audio", "image"
    preset: str
    on_progress: ProgressCallback | None = None


# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------

# Video presets: (scale_filter, vcodec, crf, acodec, extra_args)
_VIDEO_PRESETS: dict[str, dict[str, Any]] = {
    "mp4_1080": {"vf": "scale=-2:1080", "vcodec": "libx264", "crf": 23,
                 "acodec": "aac", "ab": "128k", "extra": ["-movflags", "+faststart"]},
    "mp4_720":  {"vf": "scale=-2:720",  "vcodec": "libx264", "crf": 28,
                 "acodec": "aac", "ab": "128k", "extra": ["-movflags", "+faststart"]},
    "mp4_480":  {"vf": "scale=-2:480",  "vcodec": "libx264", "crf": 30,
                 "acodec": "aac", "ab": "96k",  "extra": ["-movflags", "+faststart"]},
    "mp4_audio": {"vf": None, "vcodec": None, "crf": None,
                  "acodec": "libmp3lame", "ab": "192k", "extra": ["-vn"]},
}

# Audio presets: (acodec, bitrate)
_AUDIO_PRESETS: dict[str, dict[str, Any]] = {
    "mp3_320": {"acodec": "libmp3lame", "ab": "320k"},
    "mp3_192": {"acodec": "libmp3lame", "ab": "192k"},
    "mp3_128": {"acodec": "libmp3lame", "ab": "128k"},
    "mp3_64":  {"acodec": "libmp3lame", "ab": "64k"},
    "m4a_256": {"acodec": "aac", "ab": "256k"},
    "m4a_128": {"acodec": "aac", "ab": "128k"},
    "m4a_64":  {"acodec": "aac", "ab": "64k"},
}

# Image presets: (quality, max_dim)
_IMAGE_PRESETS: dict[str, dict[str, Any]] = {
    "jpg_85":  {"format": "mjpeg", "q": 85,  "max_dim": IMAGE_MAX_DIM},
    "jpg_75":  {"format": "mjpeg", "q": 75,  "max_dim": IMAGE_MAX_DIM},
    "jpg_60":  {"format": "mjpeg", "q": 60,  "max_dim": IMAGE_MAX_DIM},
    "jpg_40":  {"format": "mjpeg", "q": 40,  "max_dim": IMAGE_MAX_DIM},
    "png":     {"format": "png",   "q": None, "max_dim": IMAGE_MAX_DIM},
}


def video_presets() -> dict[str, str]:
    """Human-readable labels for video presets."""
    return {
        "mp4_1080": "MP4 1080p",
        "mp4_720":  "MP4 720p",
        "mp4_480":  "MP4 480p",
        "mp4_audio": "Audio only (MP3)",
    }


def audio_presets() -> dict[str, str]:
    """Human-readable labels for audio presets."""
    return {
        "mp3_320": "MP3 320k",
        "mp3_192": "MP3 192k",
        "mp3_128": "MP3 128k",
        "mp3_64":  "MP3 64k",
        "m4a_256": "M4A 256k",
        "m4a_128": "M4A 128k",
        "m4a_64":  "M4A 64k",
    }


def image_presets() -> dict[str, str]:
    """Human-readable labels for image presets."""
    return {
        "jpg_85":  "JPEG 85%",
        "jpg_75":  "JPEG 75%",
        "jpg_60":  "JPEG 60%",
        "jpg_40":  "JPEG 40%",
        "png":     "PNG",
    }


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

async def probe_media(path: Path) -> MediaInfo:
    """Probe a media file with ffprobe and return best-effort metadata."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists():
        return MediaInfo(kind="unknown", size_bytes=_file_size(path))
    args = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception:  # noqa: BLE001
        log.debug("ffprobe failed for %s", path, exc_info=True)
        return MediaInfo(kind="unknown", size_bytes=_file_size(path))
    if proc.returncode != 0 or not stdout:
        return MediaInfo(kind="unknown", size_bytes=_file_size(path))
    try:
        data = json.loads(stdout)
    except (TypeError, ValueError):
        return MediaInfo(kind="unknown", size_bytes=_file_size(path))

    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_s = None
    for src in (fmt, video_stream, audio_stream):
        if not src:
            continue
        for key in ("duration", "duration_ts"):
            val = src.get(key)
            if val is not None:
                try:
                    duration_s = float(val)
                    break
                except (TypeError, ValueError):
                    pass
        if duration_s is not None:
            break

    kind = "unknown"
    if video_stream:
        kind = "video"
    elif audio_stream:
        kind = "audio"
    elif any(s.get("codec_type") == "subtitle" for s in streams):
        kind = "image"  # treat subtitle-only as unsupported image-like
    else:
        kind = "image"

    return MediaInfo(
        kind=kind,
        mime=fmt.get("format_name"),
        width=video_stream.get("width") if video_stream else None,
        height=video_stream.get("height") if video_stream else None,
        duration_s=duration_s,
        size_bytes=_file_size(path),
        vcodec=video_stream.get("codec_name") if video_stream else None,
        acodec=audio_stream.get("codec_name") if audio_stream else None,
    )


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


# ---------------------------------------------------------------------------
# ffmpeg argument builders
# ---------------------------------------------------------------------------

def build_convert_args(
    job: ConvertJob,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> list[str]:
    """Assemble the ffmpeg argument list for ``job``."""
    inp = job.input_path
    out = job.output_path
    mt = job.media_type
    preset = job.preset

    args = ["-y", "-hide_banner", "-loglevel", "error", "-i", str(inp)]

    if mt == "video":
        spec = _VIDEO_PRESETS.get(preset)
        if spec is None:
            raise ValueError(f"unknown video preset {preset!r}")
        if spec["vf"]:
            args += ["-vf", spec["vf"]]
        if spec["vcodec"]:
            args += ["-c:v", spec["vcodec"], "-preset", "veryfast"]
            if spec["crf"] is not None:
                args += ["-crf", str(spec["crf"])]
        else:
            args += ["-vn"]
        if spec["acodec"]:
            args += ["-c:a", spec["acodec"], "-b:a", spec["ab"]]
        else:
            args += ["-an"]
        args += spec["extra"]
        args += ["-movflags", "+faststart"]

    elif mt == "audio":
        spec = _AUDIO_PRESETS.get(preset)
        if spec is None:
            raise ValueError(f"unknown audio preset {preset!r}")
        args += ["-vn", "-c:a", spec["acodec"], "-b:a", spec["ab"]]

    elif mt == "image":
        spec = _IMAGE_PRESETS.get(preset)
        if spec is None:
            raise ValueError(f"unknown image preset {preset!r}")
        args += ["-vn", "-an"]
        if spec["format"] == "mjpeg":
            args += ["-c:v", "mjpeg", "-q:v", str(spec["q"])]
        else:
            args += ["-c:v", "png"]
        if spec["max_dim"]:
            args += ["-vf", f"scale='min(iw,{spec['max_dim']}):min(ih,{spec['max_dim']}):force_original_aspect_ratio=decrease'"]
    else:
        raise ValueError(f"unknown media type {mt!r}")

    args.append(str(out))
    return args


async def _run_ffmpeg_progress(
    args: list[str],
    *,
    timeout: int,
    duration_s: float = 0.0,
    on_progress: ProgressCallback | None = None,
) -> bool:
    """Run ffmpeg, reporting progress via ``on_progress`` (0-99)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    full_args = [ffmpeg, *args]
    proc = await asyncio.create_subprocess_exec(
        *full_args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def _consume() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            if on_progress and duration_s > 0:
                match = re.search(rb"out_time_ms=(\d+)", line)
                if match:
                    seconds = int(match.group(1)) / 1_000_000
                    await on_progress(min(99.0, seconds / duration_s * 100))
        await proc.wait()

    try:
        await asyncio.wait_for(_consume(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(OSError):
            await proc.wait()
        return False
    return proc.returncode in (0, None)


async def convert_media(
    job: ConvertJob,
    *,
    semaphore: asyncio.Semaphore | None = None,
    timeout: int | None = None,
    duration_s: float = 0.0,
) -> bool:
    """Convert ``job.input_path`` to ``job.output_path``. Returns ``True`` on success."""
    if not shutil.which("ffmpeg"):
        return False
    if timeout is None:
        timeout = 3600
    args = build_convert_args(job, semaphore=semaphore)

    # Acquire the concurrency slot so ffmpeg does not overwhelm the host.
    acquire = semaphore if semaphore is not None else contextlib.nullcontext()
    async with acquire():
        ok = await _run_ffmpeg_progress(
            args,
            timeout=timeout,
            duration_s=duration_s,
            on_progress=job.on_progress,
        )
    return ok
