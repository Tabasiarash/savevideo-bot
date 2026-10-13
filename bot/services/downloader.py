"""yt-dlp wrapper.

Everything is executed as a subprocess (``python -m yt_dlp …``) so that hung
processes can be killed on timeout, and long-running work never blocks the
event loop. Progress is parsed from the ``[download] …%`` lines yt-dlp emits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shlex
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bot.config import Settings

log = logging.getLogger(__name__)

_PROGRESS_RE = re.compile(r"\[download\]\s+([\d.]+)%")

_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

ProgressCallback = Callable[[float, int, int], Awaitable[None]]
VoidCallback = Callable[[], Awaitable[None]]


class YtDlpError(Exception):
    """Base error with a user-presentable message."""


class InfoError(YtDlpError):
    pass


class DownloadError(YtDlpError):
    pass


class TimeoutError_(YtDlpError):
    pass


@dataclass
class DownloadJob:
    """Description of a single media download requested by the user."""

    url: str
    format_key: str  # video_720 / video_best / audio_mp3 / audio_m4a / thumbnail
    tmpdir: Path
    queue_position: int = 0
    height: int | None = None
    audio_codec: str | None = None
    playlist: bool = False
    playlist_items: str | None = None
    title_hint: str | None = None


@dataclass
class DownloadResult:
    """Outcome of a download: produced media files plus metadata."""

    paths: list[Path] = field(default_factory=list)
    thumbnail: Path | None = None
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    width: int | None = None
    height: int | None = None
    ext: str = ""
    site: str = ""
    total_bytes: int = 0
    original_format: str = ""

    @property
    def single_path(self) -> Path | None:
        return self.paths[0] if self.paths else None

    def add_result(self, info: dict[str, Any]) -> None:
        self.title = str(info.get("title") or self.title)
        self.uploader = str(info.get("uploader") or info.get("channel") or "")
        self.duration = float(info.get("duration") or 0.0)
        self.site = str(info.get("extractor_key") or "").lower()
        if info.get("width"):
            self.width = int(info["width"])
        if info.get("height"):
            self.height = int(info["height"])


@dataclass
class EgressProbe:
    """Result of a live metadata-only probe used to validate network egress."""

    ok: bool
    detail: str


class YtDlpRunner:
    """Runs the yt-dlp CLI as a killable subprocess."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _extra_args(
        self,
        *,
        playlist: bool,
        playlist_items: str | None,
        url: str | None = None,
        cookiejar_dir: Path | None = None,
    ) -> list[str]:
        args = ["--no-warnings", "--no-colors", "--newline", "-q"]
        if playlist:
            args += ["--yes-playlist"]
            if playlist_items:
                args += ["--playlist-items", playlist_items]
        else:
            args += ["--no-playlist"]
        args += self._cookie_args(cookiejar_dir)
        if self.settings.proxy_url:
            args += ["--proxy", self.settings.proxy_url]
        if self.settings.force_ipv4:
            args += ["--force-ipv4"]
        elif self.settings.force_ipv6 and _is_youtube_url(url):
            args += ["--force-ipv6"]
        runtime = detect_js_runtime(self.settings)
        if runtime:
            args += ["--js-runtimes", runtime]
        args += ["--socket-timeout", "30", "--retries", "3", "--fragment-retries", "3"]
        args += shlex.split(self.settings.ytdlp_extra_args or "")
        return args

    def _cookie_args(self, cookiejar_dir: Path | None) -> list[str]:
        """``--cookies`` args, staged into a writable dir when one is given.

        yt-dlp rewrites the cookie file on exit, so the configured file must be
        writable by the service — but it may live somewhere read-only (e.g.
        ``/etc`` under ``ProtectSystem=full``) and shared across jobs. When a
        ``cookiejar_dir`` is available the cookies are copied into a private
        ``.cookies/`` subdirectory (never mistaken for media) for the run.
        """
        source = self.settings.cookies_file
        if not source or not Path(source).exists():
            return []
        dest = Path(source)
        if cookiejar_dir is not None:
            try:
                staged = Path(cookiejar_dir) / ".cookies"
                staged.mkdir(parents=True, exist_ok=True)
                dest = staged / "cookies.txt"
                shutil.copyfile(source, dest)
                os.chmod(dest, 0o600)
            except OSError:
                log.warning("could not stage cookies, using source file", exc_info=True)
                dest = Path(source)
        return ["--cookies", str(dest)]

    def _make_cookiejar_dir(self) -> Path | None:
        """Scratch dir for cookie staging when a command has no job tmpdir."""
        try:
            base = Path(self.settings.temp_dir_resolved)
            base.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(dir=base, prefix="cookiejar-"))
        except OSError:
            return None

    async def extract_info(
        self,
        url: str,
        *,
        playlist: bool = False,
        playlist_items: str | None = None,
        cookiejar_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Extract metadata as a dict (``yt-dlp -J``). Raises :class:`InfoError`."""
        args = [
            sys.executable,
            "-m",
            "yt_dlp",
            "-J",
            "--skip-download",
            *self._extra_args(
                playlist=playlist, playlist_items=playlist_items, url=url, cookiejar_dir=cookiejar_dir
            ),
            url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.settings.info_timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise InfoError("info extraction timed out") from exc
        if proc.returncode != 0:
            msg = err.decode("utf-8", "replace").strip() or "unknown error"
            raise InfoError(msg[-500:])
        import json

        try:
            return json.loads(out.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise InfoError("invalid metadata from extractor") from exc

    async def _run_and_read_progress(
        self,
        args: list[str],
        *,
        on_progress: ProgressCallback | None,
        timeout: int,
        start_callback: VoidCallback | None = None,
    ) -> None:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if start_callback is not None:
            await start_callback()

        async def _consume() -> None:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                await self._handle_line(line, on_progress)
            await process.wait()

        task = asyncio.create_task(_consume())
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except TimeoutError:
            process.kill()
            with contextlib.suppress(OSError):
                await process.wait()
            raise TimeoutError_(f"process killed after {timeout}s timeout") from None
        if process.returncode not in (0, None):
            raise DownloadError(f"exit code {process.returncode}")

    async def _handle_line(self, line: bytes, on_progress: ProgressCallback | None) -> None:
        if not on_progress:
            return
        text = line.decode("utf-8", "replace")
        match = _PROGRESS_RE.search(text)
        if not match:
            return
        percent = float(match.group(1))
        total = 0
        tm = re.search(r"of\s+~?\s*([\d.]+)([KMG]i?B)", text)
        if tm:
            total = _parse_size(tm.group(1), tm.group(2))
        await on_progress(percent, 0, total)

    async def download(
        self,
        job: DownloadJob,
        *,
        on_progress: ProgressCallback | None = None,
        start_callback: VoidCallback | None = None,
        write_thumbnail: bool = False,
    ) -> list[Path]:
        """Run a download job inside ``job.tmpdir``; returns produced media files."""
        safe = job.title_hint or "media"
        base = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", safe)[:120] or "media"
        outtmpl = str(job.tmpdir / f"{base}.%(ext)s")

        args = [
            sys.executable,
            "-m",
            "yt_dlp",
            *self._extra_args(
                playlist=job.playlist,
                playlist_items=job.playlist_items,
                url=job.url,
                cookiejar_dir=job.tmpdir,
            ),
            "-o",
            outtmpl,
            "--merge-output-format",
            "mp4",
        ]
        args += self._format_args(job.format_key, job.height, job.audio_codec)
        if write_thumbnail:
            args += ["--write-thumbnail", "--convert-thumbnails", "jpg"]
        args.append(job.url)

        if job.format_key.startswith("audio"):
            args += ["-x"]

        await self._run_and_read_progress(
            args,
            on_progress=on_progress,
            timeout=self.settings.download_timeout,
            start_callback=start_callback,
        )
        return _collect_media_files(job.tmpdir)

    @staticmethod
    def _format_args(format_key: str, height: int | None, audio_codec: str | None) -> list[str]:
        if format_key == "video_best":
            return ["-f", "bv*+ba/b"]
        if format_key.startswith("video_"):
            spec = f"bv*[height<={height}]+ba/b[height<={height}]" if height else "bv*+ba/b"
            return ["-f", spec]
        if format_key == "audio_mp3":
            return ["-f", "ba/b", "--audio-format", "mp3", "--audio-quality", "0"]
        if format_key == "audio_m4a":
            codec = audio_codec or "m4a"
            return ["-f", f"ba[ext={codec}]/ba/b", "--audio-format", codec]
        raise DownloadError(f"unknown format key: {format_key}")

    async def download_thumbnail(self, job: DownloadJob) -> Path | None:
        safe = job.title_hint or "media"
        base = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", safe)[:120] or "media"
        outtmpl = str(job.tmpdir / f"{base}.%(ext)s")
        args = [
            sys.executable,
            "-m",
            "yt_dlp",
            *self._extra_args(
                playlist=False, playlist_items=None, url=job.url, cookiejar_dir=job.tmpdir
            ),
            "--skip-download",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "-o",
            outtmpl,
            job.url,
        ]
        try:
            await self._run_and_read_progress(args, on_progress=None, timeout=self.settings.download_timeout)
        except DownloadError:
            return None
        for ext in ("jpg", "png", "webp"):
            cand = job.tmpdir / f"{base}.{ext}"
            if cand.exists():
                return cand
        for ext in ("jpg", "png", "webp"):
            cand = job.tmpdir / f"{base}.{ext}"
            if cand.exists():
                return cand
        return None

    async def probe_egress(self, url: str | None = None) -> EgressProbe:
        """Metadata-only yt-dlp call to check that the configured egress works.

        Uses the same proxy / cookies / IP-family / JS-runtime options as a real
        download, so a success here means downloads should succeed too.
        """
        target = (url or self.settings.egress_test_url or "").strip()
        if not target:
            return EgressProbe(ok=False, detail="no test url configured")
        staged_dir = self._make_cookiejar_dir()
        try:
            args = [
                sys.executable,
                "-m",
                "yt_dlp",
                *self._extra_args(
                    playlist=False, playlist_items=None, url=target, cookiejar_dir=staged_dir
                ),
                "--skip-download",
                "--no-progress",
                "--print",
                "%(title)s",
                target,
            ]
            return await self._run_probe(args)
        finally:
            if staged_dir is not None:
                shutil.rmtree(staged_dir, ignore_errors=True)

    async def _run_probe(self, args: list[str]) -> EgressProbe:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as exc:
            return EgressProbe(ok=False, detail=f"spawn failed: {exc}")
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=max(self.settings.info_timeout, 90)
            )
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(OSError):
                await proc.wait()
            return EgressProbe(ok=False, detail="timeout")
        if proc.returncode == 0:
            lines = out.decode("utf-8", "replace").strip().splitlines()
            return EgressProbe(ok=True, detail=(lines[0][:100] if lines else "ok"))
        return EgressProbe(ok=False, detail=classify_egress_error(err.decode("utf-8", "replace")))


# --------------------------------------------------------------------------
# Pure helpers (unit-testable without the CLI)
# --------------------------------------------------------------------------

def _is_youtube_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _YT_HOSTS)


def detect_js_runtime(settings: Settings) -> str | None:
    """Pick the JS runtime for yt-dlp's extraction (``--js-runtimes``).

    ``JS_RUNTIME=auto`` prefers Deno (yt-dlp's recommended runtime) and falls
    back to Node. An explicit value is honoured if the binary is on PATH, or
    passed through verbatim when it carries a ``name:path`` override.
    """
    requested = (getattr(settings, "js_runtime", "") or "").strip()
    if requested and requested.lower() != "auto":
        name = requested.split(":", 1)[0]
        if ":" in requested or shutil.which(name):
            return requested
        return None
    for name in ("deno", "node"):
        if shutil.which(name):
            return name
    return None


def curl_cffi_version() -> str:
    """Version of the optional ``curl_cffi`` TLS-impersonation backend, if any."""
    try:
        import curl_cffi
    except Exception:  # noqa: BLE001
        return ""
    return str(getattr(curl_cffi, "__version__", "installed"))


def mask_proxy(url: str) -> str:
    """Render a proxy URL for logs/telemetry with credentials removed."""
    if not url:
        return "off"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "set"
    if not parts.hostname:
        return "set"
    host = parts.hostname
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host
    if parts.port:
        netloc += f":{parts.port}"
    if parts.username or parts.password:
        netloc = "***@" + netloc
    return f"{parts.scheme}://{netloc}{parts.path or ''}"


def classify_egress_error(text: str) -> str:
    """Reduce a yt-dlp stderr blob to a short, actionable failure class."""
    lowered = (text or "").lower()
    if "sign in to confirm" in lowered or "not a bot" in lowered:
        return "bot-wall (needs cookies or a different IP)"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "no video formats" in lowered or "player response" in lowered:
        return "no-formats"
    if any(
        k in lowered
        for k in ("unable to download", "transporterror", "network", "connection", "resolve", "ssl")
    ):
        return "network"
    return "error"


def _parse_size(number: str, unit: str) -> int:
    unit = unit.replace("i", "").replace("I", "").upper()
    mult = {"B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2, "G": 1024**3, "GB": 1024**3}
    return int(float(number) * mult.get(unit, 1))


def _collect_media_files(tmpdir: Path) -> list[Path]:
    excluded_suffixes = {".part", ".ytdl", ".json", ".info.json", ".jpg", ".jpeg", ".png", ".webp"}
    files = [p for p in tmpdir.iterdir() if p.is_file()]
    media = [
        p
        for p in files
        if p.suffix not in excluded_suffixes and not any(p.name.endswith(s) for s in (".part", ".ytdl"))
    ]
    return sorted(media, key=lambda p: p.stat().st_size, reverse=True)


def ffmpeg_binary() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


async def transcode_to_size(
    input_path: Path,
    outdir: Path,
    limit_bytes: int,
    duration_s: float,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Re-encode ``input_path`` so the output fits ``limit_bytes`` using libx264.

    Returns the new file path. Raises :class:`DownloadError` if it cannot be
    made to fit (bitrate floor impossible).
    """
    from bot.utils import format_size, target_bitrate_bps

    video_bps = target_bitrate_bps(limit_bytes, duration_s) - 128_000  # 128 kbps audio
    if video_bps <= 80_000:
        raise DownloadError(f"cannot fit {format_size(input_path.stat().st_size)} into the upload limit")

    out = outdir / f"{input_path.stem}_fitsize.mp4"
    ffmpeg = ffmpeg_binary()
    args = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        str(int(video_bps)),
        "-maxrate",
        str(int(video_bps * 1.1)),
        "-bufsize",
        str(int(video_bps * 2)),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.STDOUT
    )
    if on_progress:
        await on_progress(0.0, 0, 0)
    await process.wait()
    if process.returncode != 0 or not out.exists():
        raise DownloadError("re-encode with ffmpeg failed")
    return out


async def transcode_audio_to_size(
    input_path: Path,
    outdir: Path,
    limit_bytes: int,
    duration_s: float,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Re-encode audio to the max bitrate that fits ``limit_bytes``."""
    from bot.utils import format_size, target_bitrate_bps

    audio_bps = int(target_bitrate_bps(limit_bytes, duration_s) * 0.9)
    if audio_bps < 32_000:
        raise DownloadError(f"audio too long to fit {format_size(limit_bytes)}")

    out = outdir / f"{input_path.stem}_fitsize.mp3"
    ffmpeg = ffmpeg_binary()
    args = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        str(audio_bps),
        str(out),
    ]
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.STDOUT
    )
    if on_progress:
        await on_progress(0.0, 0, 0)
    await process.wait()
    if process.returncode != 0 or not out.exists():
        raise DownloadError("audio re-encode with ffmpeg failed")
    return out


async def probe_duration(path: Path) -> float:
    """Best-effort duration via ffprobe (off the event loop)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    import subprocess

    def _run() -> float:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
                 "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return float(result.stdout.strip())
        except (ValueError, subprocess.SubprocessError, OSError):
            return 0.0

    return await asyncio.to_thread(_run)


def ensure_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def disk_free_bytes(path: os.PathLike | str) -> int:
    """Free disk space on the filesystem containing ``path``."""
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def cleanup_dir(path: Path) -> None:
    """Recursively delete ``path`` if it exists."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------
# Format / quality option builder (pure, unit-testable)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FormatOption:
    """One selectable quality button."""

    key: str
    label: str
    size_bytes: int | None = None
    height: int | None = None


def _option_size(info: dict[str, Any], fmt: dict[str, Any]) -> int | None:
    duration = info.get("duration")
    if fmt.get("filesize"):
        return int(fmt["filesize"])
    if fmt.get("filesize_approx"):
        return int(fmt["filesize_approx"])
    tbr = fmt.get("tbr")
    if tbr and duration:
        return int(tbr * 1000 / 8 * duration)
    return None


def estimate_format_size(info: dict[str, Any], height: int) -> int | None:
    """Best-guess download size for a given height (merged video+audio)."""
    formats = info.get("formats") or []
    at_height = [f for f in formats if f.get("height") == height]
    has_video = [f for f in at_height if f.get("vcodec") and f.get("vcodec") != "none"]
    if not has_video:
        return None
    combined = [f for f in has_video if f.get("acodec") and f.get("acodec") != "none"]
    if combined:
        sizes = [_option_size(info, f) for f in combined]
        sizes = [s for s in sizes if s]
        return max(sizes) if sizes else None
    v_sizes = [_option_size(info, f) for f in has_video]
    v_sizes = [s for s in v_sizes if s]
    video_size = max(v_sizes) if v_sizes else None
    audio_size = None
    audios = [f for f in formats if f.get("acodec") and f.get("acodec") != "none" and f.get("vcodec") in (None, "none")]
    a_sizes = [_option_size(info, f) for f in audios]
    a_sizes = [s for s in a_sizes if s]
    if a_sizes:
        audio_size = max(a_sizes)
    if video_size is None:
        return audio_size
    if audio_size is None:
        return video_size
    return video_size + audio_size


def best_estimate(info: dict[str, Any]) -> int | None:
    """Size estimate for the "Best" option (top height merged with best audio)."""
    height = max((f["height"] for f in info.get("formats") or [] if f.get("height")), default=0)
    if not height:
        sizes = []
        for f in info.get("formats") or []:
            if f.get("acodec") and f.get("vcodec") and f.get("vcodec") != "none":
                s = _option_size(info, f)
                if s:
                    sizes.append(s)
        return max(sizes) if sizes else None
    return estimate_format_size(info, height)


def video_quality_rows(
    info: dict[str, Any],
    prefix: str = "dl",
    max_heights: int = 6,
) -> list[list[FormatOption]]:
    """Build rows of video quality buttons from a real yt-dlp info dict.

    Heights are gathered from actual formats, deduplicated, capped at
    ``max_heights`` (spread evenly), followed by a "Best" entry.
    """
    from bot.utils import format_size

    heights = sorted(
        {
            int(f["height"])
            for f in info.get("formats") or []
            if f.get("height") and f.get("vcodec") and f.get("vcodec") != "none"
        },
        reverse=True,
    )
    if not heights:
        return []

    spread = list(range(0, len(heights), max(1, len(heights) // max_heights)))
    if len(heights) <= max_heights:
        picked = heights
    else:
        picked = [heights[max(min(i, len(heights) - 1), 0)] for i in spread]
        if heights[0] not in picked:
            picked = [heights[0]] + picked[:-1]
    picked = sorted(set(picked), reverse=True)

    rows: list[list[FormatOption]] = []
    for h in picked:
        size = estimate_format_size(info, h)
        label = f"{h}p"
        if size:
            label += f" · {format_size(size)}"
        rows.append([FormatOption(key=f"{prefix}:video_{h}", label=label, size_bytes=size, height=h)])

    best_size = best_estimate(info)
    best_label = "Best"
    if best_size:
        best_label += f" · {format_size(best_size)}"
    rows.append([FormatOption(key=f"{prefix}:video_best", label=best_label, size_bytes=best_size)])
    return rows
