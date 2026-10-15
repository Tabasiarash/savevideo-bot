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
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bot.config import Settings

log = logging.getLogger(__name__)

_PROGRESS_RE = re.compile(r"\[download\]\s+([\d.]+)%")

_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_INSTAGRAM_HOSTS = ("instagram.com", "instagr.am")
_TIKTOK_HOSTS = ("tiktok.com",)
_X_HOSTS = ("x.com", "twitter.com")
_SOUNDCLOUD_HOSTS = ("soundcloud.com", "api.soundcloud.com")
EGRESS_CACHE_TTL = 300.0
# Lightweight proxy-reachability check (see probe_proxy_egress).
PROXY_EXIT_CACHE_TTL = 60.0
PROXY_EXIT_TIMEOUT = 3.0
PROXY_EXIT_URL = "https://ifconfig.me/ip"
# Latch so a down proxy logs exactly one warning per outage (no retry-storm).
_proxy_warned = [False]

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
        self._yt_sem = asyncio.Semaphore(max(1, settings.yt_max_concurrency))
        self._egress_cache: tuple[float, str, EgressProbe] | None = None
        self._proxy_exit_cache: tuple[float, EgressProbe] | None = None

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
        proxy = _platform_proxy(self.settings, url)
        if proxy:
            args += ["--proxy", proxy]
        # The IP-family overrides can fight the proxy's routing, so only apply
        # them when talking to the origin directly.
        elif self.settings.force_ipv4:
            args += ["--force-ipv4"]
        elif self.settings.force_ipv6 and _is_youtube_url(url):
            args += ["--force-ipv6"]
        runtime = detect_js_runtime(self.settings)
        if runtime:
            args += ["--js-runtimes", runtime]
        args += [
            "--socket-timeout",
            "30",
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "--sleep-requests",
            "1",
        ]
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
        if job.format_key.startswith("video"):
            # Put the moov atom first so uploads/seeking are efficient.
            args += ["--postprocessor-args", "Merger:-movflags +faststart"]
        args += self._format_args(job.format_key, job.height, job.audio_codec)
        if write_thumbnail:
            args += ["--write-thumbnail", "--convert-thumbnails", "jpg"]
        args.append(job.url)

        if job.format_key.startswith("audio"):
            args += ["-x"]

        async with self._yt_sem:
            await self._run_and_read_progress(
                args,
                on_progress=on_progress,
                timeout=self.settings.download_timeout,
                start_callback=start_callback,
            )
        return _collect_media_files(job.tmpdir)

    @staticmethod
    def _format_args(
        format_key: str,
        height: int | None,
        audio_codec: str | None,
    ) -> list[str]:
        if format_key == "video_best":
            # No height cap: "Best" always means the highest quality available.
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
        now = time.monotonic()
        cached = self._egress_cache
        if cached and cached[1] == target and now - cached[0] < EGRESS_CACHE_TTL:
            return cached[2]
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
            result = await self._run_probe(args)
        finally:
            if staged_dir is not None:
                shutil.rmtree(staged_dir, ignore_errors=True)
        self._egress_cache = (now, target, result)
        return result

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

    async def probe_proxy_egress(self) -> EgressProbe:
        """Fetch the public exit IP through the configured proxy.

        Unlike :meth:`probe_egress` this does not run yt-dlp: it only verifies
        that the proxy is reachable and reports the egress IP (masked). Cached
        briefly, and a down proxy logs exactly one warning per outage.
        """
        proxy = (self.settings.proxy_url or "").strip()
        if not proxy:
            return EgressProbe(ok=False, detail="proxy off")
        now = time.monotonic()
        cached = self._proxy_exit_cache
        if cached and now - cached[0] < PROXY_EXIT_CACHE_TTL:
            return cached[1]
        result = await _fetch_exit_ip(proxy)
        self._proxy_exit_cache = (now, result)
        if result.ok:
            _proxy_warned[0] = False
        elif not _proxy_warned[0]:
            log.warning("proxy egress check failed: %s", result.detail)
            _proxy_warned[0] = True
        return result


# --------------------------------------------------------------------------
# Pure helpers (unit-testable without the CLI)
# --------------------------------------------------------------------------

def _url_host(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(host: str, hosts: tuple[str, ...]) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def _is_youtube_url(url: str | None) -> bool:
    return _host_matches(_url_host(url), _YT_HOSTS)


def _platform_proxy(settings: Settings, url: str | None) -> str:
    """Pick the proxy for ``url``: a per-platform override, else ``PROXY_URL``."""
    host = _url_host(url)
    if host:
        if _host_matches(host, _INSTAGRAM_HOSTS):
            return settings.proxy_url_instagram or settings.proxy_url
        if _host_matches(host, _TIKTOK_HOSTS):
            return settings.proxy_url_tiktok or settings.proxy_url
        if _host_matches(host, _X_HOSTS):
            return settings.proxy_url_x or settings.proxy_url
        if _host_matches(host, _SOUNDCLOUD_HOSTS):
            return settings.proxy_url_soundcloud or settings.proxy_url
    return settings.proxy_url


_NODE_MIN_MAJOR = 22


def _node_version() -> str | None:
    path = shutil.which("node")
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip().lstrip("v") or None


def _node_supported() -> bool:
    version = _node_version()
    if not version:
        return False
    try:
        return int(version.split(".", 1)[0]) >= _NODE_MIN_MAJOR
    except ValueError:
        return False


def detect_js_runtime(settings: Settings) -> str | None:
    """Pick the JS runtime for yt-dlp's extraction (``--js-runtimes``).

    ``JS_RUNTIME=auto`` prefers Deno (yt-dlp's recommended runtime) and falls
    back to Node, but only when it is new enough (>=22) — older Node versions
    cannot solve the challenges and would just fail. An explicit value is
    honoured if the binary is on PATH, or passed through verbatim when it
    carries a ``name:path`` override.
    """
    requested = (getattr(settings, "js_runtime", "") or "").strip()
    if requested and requested.lower() != "auto":
        name = requested.split(":", 1)[0]
        if ":" in requested or shutil.which(name):
            return requested
        return None
    if shutil.which("deno"):
        return "deno"
    if _node_supported():
        return "node"
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


def format_proxy_summary(settings: Settings) -> str:
    """Masked ``PROXY_URL`` plus any per-platform overrides, for ``/health``."""
    base = mask_proxy(settings.proxy_url)
    overrides = [
        f"{label}={mask_proxy(value)}"
        for label, value in (
            ("ig", settings.proxy_url_instagram),
            ("tt", settings.proxy_url_tiktok),
            ("x", settings.proxy_url_x),
            ("sc", settings.proxy_url_soundcloud),
        )
        if value
    ]
    return f"{base} [{', '.join(overrides)}]" if overrides else base


def mask_ip(value: str | None) -> str:
    """Mask the host bits of an IP address for display/logging."""
    ip = (value or "").strip()
    if not ip:
        return "unknown"
    if ":" in ip:  # IPv6: keep the first two hextets.
        parts = [p for p in ip.split(":") if p]
        return f"{':'.join(parts[:2])}:*" if parts else "unknown"
    parts = ip.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.*.*"
    return ip


def _exit_ip(proxy: str) -> str:
    """Return the public IP seen through ``proxy`` (blocking; run in a thread)."""
    try:
        import curl_cffi.requests as requests
    except Exception:  # noqa: BLE001 - fall back to the curl binary
        return _exit_ip_curl(proxy)
    resp = requests.get(
        PROXY_EXIT_URL,
        proxies={"http": proxy, "https": proxy},
        timeout=PROXY_EXIT_TIMEOUT,
    )
    resp.raise_for_status()
    return (resp.text or "").strip()


def _exit_ip_curl(proxy: str) -> str:
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("no HTTP client available for proxy check")
    out = subprocess.run(
        [curl, "-fsS", "--max-time", str(int(PROXY_EXIT_TIMEOUT)), "--proxy", proxy, PROXY_EXIT_URL],
        capture_output=True,
        text=True,
        timeout=int(PROXY_EXIT_TIMEOUT) + 2,
        check=True,
    )
    return out.stdout.strip()


async def _fetch_exit_ip(proxy: str) -> EgressProbe:
    """Resolve the egress IP through ``proxy`` with a short timeout."""
    try:
        ip = await asyncio.wait_for(
            asyncio.to_thread(_exit_ip, proxy), timeout=PROXY_EXIT_TIMEOUT + 2
        )
    except TimeoutError:
        return EgressProbe(ok=False, detail="timeout")
    except Exception as exc:  # noqa: BLE001
        return EgressProbe(ok=False, detail=f"error: {type(exc).__name__}")
    ip = (ip or "").strip()
    if not ip:
        return EgressProbe(ok=False, detail="empty response")
    return EgressProbe(ok=True, detail=mask_ip(ip))


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


@dataclass(frozen=True)
class MediaInfo:
    """Duration / dimensions read back from a produced file via ffprobe."""

    duration: float = 0.0
    width: int | None = None
    height: int | None = None


async def probe_media(path: Path) -> MediaInfo:
    """Best-effort duration/width/height of ``path`` via ffprobe (off-loop).

    Used to tag uploads with the real metadata of the produced file rather than
    the (possibly different) requested format.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return MediaInfo()
    import json
    import subprocess

    def _run() -> MediaInfo:
        try:
            result = subprocess.run(
                [
                    ffprobe, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height:format=duration",
                    "-of", "json", str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            data = json.loads(result.stdout or "{}")
        except (ValueError, subprocess.SubprocessError, OSError):
            return MediaInfo()
        stream = (data.get("streams") or [{}])[0]
        try:
            duration = float((data.get("format") or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        width = stream.get("width")
        height = stream.get("height")
        return MediaInfo(
            duration=duration,
            width=int(width) if width else None,
            height=int(height) if height else None,
        )

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
    over_limit: bool = False


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
    """Size estimate for the "Best" option (highest height merged with best audio)."""
    formats = info.get("formats") or []
    heights = [f["height"] for f in formats if f.get("height")]
    height = max(heights, default=0)
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
    limit_bytes: int | None = None,
) -> list[list[FormatOption]]:
    """Build rows of video quality buttons from a real yt-dlp info dict.

    Every distinct height is listed (no cap, no spreading) followed by a "Best"
    entry for the highest quality. When ``limit_bytes`` is given, options whose
    estimate exceeds the limit are flagged ``over_limit`` so the caller can mark
    them for link delivery — they are never hidden or filtered out.
    """
    from bot.utils import fits_upload_limit, format_size

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

    def _over_limit(size: int | None) -> bool:
        return bool(size and limit_bytes and not fits_upload_limit(size, limit_bytes))

    rows: list[list[FormatOption]] = []
    for h in heights:
        size = estimate_format_size(info, h)
        label = f"{h}p"
        if size:
            label += f" · {format_size(size)}"
        rows.append(
            [
                FormatOption(
                    key=f"{prefix}:video_{h}",
                    label=label,
                    size_bytes=size,
                    height=h,
                    over_limit=_over_limit(size),
                )
            ]
        )

    best_size = best_estimate(info)
    best_label = "Best"
    if best_size:
        best_label += f" · {format_size(best_size)}"
    rows.append(
        [
            FormatOption(
                key=f"{prefix}:video_best",
                label=best_label,
                size_bytes=best_size,
                over_limit=_over_limit(best_size),
            )
        ]
    )
    return rows
