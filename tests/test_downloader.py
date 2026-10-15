from __future__ import annotations

from pathlib import Path

import pytest
from bot.config import Settings
from bot.services import downloader as dl
from bot.services.downloader import (
    DownloadError,
    DownloadJob,
    YtDlpRunner,
    _collect_media_files,
    _is_youtube_url,
    _parse_size,
    _platform_proxy,
    best_estimate,
    classify_egress_error,
    curl_cffi_version,
    detect_js_runtime,
    disk_free_bytes,
    ensure_ffmpeg_available,
    estimate_format_size,
    format_proxy_summary,
    mask_ip,
    mask_proxy,
    video_quality_rows,
)


def _settings(**kwargs) -> Settings:
    base = {"_env_file": None, "bot_token": "x"}
    base.update(kwargs)
    return Settings(**base)


def test_format_args_video():
    assert YtDlpRunner._format_args("video_720", 720, None) == [
        "-f",
        "bv*[height<=720]+ba/b[height<=720]",
    ]
    # "Best" must never be capped: it is the highest quality available.
    assert YtDlpRunner._format_args("video_best", None, None) == ["-f", "bv*+ba/b"]


def test_format_args_audio():
    assert YtDlpRunner._format_args("audio_mp3", None, None)[0] == "-f"
    m4a = YtDlpRunner._format_args("audio_m4a", None, "m4a")
    assert m4a == ["-f", "ba[ext=m4a]/ba/b", "--audio-format", "m4a"]


def test_format_args_unknown_raises():
    with pytest.raises(DownloadError):
        YtDlpRunner._format_args("nope", None, None)


def test_parse_size():
    assert _parse_size("1", "B") == 1
    assert _parse_size("1", "KiB") == 1024
    assert _parse_size("1.5", "MiB") == int(1.5 * 1024 * 1024)


def test_collect_media_files_filters_and_sorts(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x" * 10)
    (tmp_path / "b.mp4").write_bytes(b"x" * 100)
    (tmp_path / "b.jpg").write_bytes(b"x" * 50)
    (tmp_path / "c.mp4.part").write_bytes(b"x" * 5)
    (tmp_path / "info.json").write_bytes(b"{}")
    result = _collect_media_files(tmp_path)
    assert [p.name for p in result] == ["b.mp4", "a.mp4"]


async def test_handle_line_progress():
    captured: list[tuple[float, int, int]] = []

    async def on_progress(percent, downloaded, total):
        captured.append((percent, downloaded, total))

    runner = YtDlpRunner(_settings())
    await runner._handle_line(b"[download]  42.5% of 10.00MiB at 1MiB/s", on_progress)
    assert captured == [(42.5, 0, 10 * 1024 * 1024)]


async def test_handle_line_ignores_non_progress():
    captured = []

    async def on_progress(percent, downloaded, total):
        captured.append(percent)

    runner = YtDlpRunner(_settings())
    await runner._handle_line(b"[youtube] abc123: Downloading webpage", on_progress)
    assert captured == []


async def test_handle_line_without_callback():
    runner = YtDlpRunner(_settings())
    await runner._handle_line(b"[download] 1%", None)


INFO = {
    "title": "T",
    "duration": 60,
    "formats": [
        {"height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 40_000_000},
        {"format_id": "136", "height": 720, "width": 1280, "vcodec": "avc1", "acodec": "none", "filesize": 20_000_000},
        {"format_id": "135", "height": 480, "width": 854, "vcodec": "avc1", "acodec": "none", "filesize": 10_000_000},
        {"format_id": "140", "height": None, "vcodec": "none", "acodec": "mp4a", "filesize": 2_000_000},
    ],
}


def test_video_quality_rows_builds_keyboard_data():
    rows = video_quality_rows(INFO, prefix="dl")
    keys = [opts[0].key for opts in rows]
    assert keys[-1] == "dl:video_best"
    assert "dl:video_720" in keys
    labels = [opts[0].label for opts in rows]
    assert any("720p" in label for label in labels)
    assert labels[-1].startswith("Best")


def test_video_quality_rows_without_video_formats():
    assert video_quality_rows({"formats": [{"height": None, "vcodec": "none"}]}) == []


def test_video_quality_rows_marks_over_limit():
    rows = video_quality_rows(INFO, prefix="dl", limit_bytes=15 * 1024 * 1024)
    by_key = {opts[0].key: opts[0] for opts in rows}
    assert by_key["dl:video_480"].over_limit is False  # ~12 MB
    assert by_key["dl:video_720"].over_limit is True  # ~22 MB
    assert by_key["dl:video_best"].over_limit is True  # ~42 MB


def test_video_quality_rows_lists_every_height_without_a_cap():
    info = {
        "duration": 60,
        "formats": [
            {"height": 2160, "vcodec": "vp9", "acodec": "none", "filesize": 900},
            {"height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 400},
        ],
    }
    rows = video_quality_rows(info, prefix="dl")
    assert [r[0].key for r in rows] == ["dl:video_2160", "dl:video_1080", "dl:video_best"]


async def test_download_adds_faststart_for_video(monkeypatch, tmp_path):
    captured: dict[str, list[str]] = {}
    runner = YtDlpRunner(_settings())

    async def fake_run(args, **kwargs):
        captured["args"] = args

    monkeypatch.setattr(runner, "_run_and_read_progress", fake_run)
    job = DownloadJob(url="https://youtu.be/x", format_key="video_720", tmpdir=tmp_path, height=720)
    await runner.download(job)
    assert "Merger:-movflags +faststart" in captured["args"]


async def test_download_omits_faststart_for_audio(monkeypatch, tmp_path):
    captured: dict[str, list[str]] = {}
    runner = YtDlpRunner(_settings())

    async def fake_run(args, **kwargs):
        captured["args"] = args

    monkeypatch.setattr(runner, "_run_and_read_progress", fake_run)
    job = DownloadJob(url="https://youtu.be/x", format_key="audio_mp3", tmpdir=tmp_path)
    await runner.download(job)
    assert "Merger:-movflags +faststart" not in captured["args"]


def test_estimate_format_size_picks_height():
    assert estimate_format_size(INFO, 480) == 12_000_000
    assert estimate_format_size(INFO, 99999) is None


def test_best_estimate_uses_top_height():
    assert best_estimate(INFO) == 42_000_000


def test_best_estimate_without_video_heights():
    info = {"duration": 60, "formats": [{"height": None, "vcodec": "avc1", "acodec": "mp4a", "filesize": 5_000_000}]}
    assert best_estimate(info) == 5_000_000


def test_helpers_on_real_paths(tmp_path):
    assert ensure_ffmpeg_available() in (True, False)
    assert disk_free_bytes(tmp_path) > 0
    job = DownloadJob(url="u", format_key="video_720", tmpdir=tmp_path)
    assert job.queue_position == 0


# --------------------------------------------------------------------------
# Anti-blocking argument construction
# --------------------------------------------------------------------------

def _no_js(monkeypatch):
    monkeypatch.setattr(dl.shutil, "which", lambda name: None)


def test_extra_args_defaults(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings())
    args = runner._extra_args(playlist=False, playlist_items=None, url="https://x.example/v")
    assert "--no-playlist" in args
    assert "--force-ipv4" not in args
    assert "--force-ipv6" not in args
    assert "--js-runtimes" not in args


def test_extra_args_flat_playlist(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings())
    assert "--flat-playlist" not in runner._extra_args(playlist=True, playlist_items=None)
    assert "--flat-playlist" in runner._extra_args(playlist=True, playlist_items=None, flat=True)


def test_extra_args_adds_js_runtime_prefers_deno(monkeypatch):
    monkeypatch.setattr(
        dl.shutil, "which", lambda name: "/usr/local/bin/deno" if name == "deno" else None
    )
    runner = YtDlpRunner(_settings())
    args = runner._extra_args(playlist=False, playlist_items=None)
    assert args[args.index("--js-runtimes") + 1] == "deno"


def test_extra_args_js_runtime_explicit_missing_skipped(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings(js_runtime="deno"))
    args = runner._extra_args(playlist=False, playlist_items=None)
    assert "--js-runtimes" not in args


def test_extra_args_splits_extra_args(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings(ytdlp_extra_args="--cookies-from-browser firefox --compat-options here"))
    args = runner._extra_args(playlist=False, playlist_items=None)
    tail = args[args.index("--cookies-from-browser"):]
    assert tail == ["--cookies-from-browser", "firefox", "--compat-options", "here"]


def test_force_ipv4_global(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings(force_ipv4=True))
    args = runner._extra_args(playlist=False, playlist_items=None, url="https://vimeo.com/1")
    assert "--force-ipv4" in args
    assert "--force-ipv6" not in args


def test_force_ip_family_skipped_when_proxy_set(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings(force_ipv4=True, proxy_url="socks5h://127.0.0.1:1080"))
    args = runner._extra_args(playlist=False, playlist_items=None, url="https://vimeo.com/1")
    assert "--proxy" in args
    assert "--force-ipv4" not in args
    assert "--force-ipv6" not in args


def test_platform_proxy_override_and_fallback():
    s = _settings(
        proxy_url="socks5h://base:1080",
        proxy_url_instagram="socks5h://ig:1080",
        proxy_url_tiktok="",
        proxy_url_x="socks5h://x:1080",
    )
    assert _platform_proxy(s, "https://www.instagram.com/reel/x/") == "socks5h://ig:1080"
    assert _platform_proxy(s, "https://www.tiktok.com/@u/video/1") == "socks5h://base:1080"
    assert _platform_proxy(s, "https://twitter.com/u/status/1") == "socks5h://x:1080"
    assert _platform_proxy(s, "https://x.com/u/status/1") == "socks5h://x:1080"
    assert _platform_proxy(s, "https://vimeo.com/1") == "socks5h://base:1080"
    assert _platform_proxy(s, None) == "socks5h://base:1080"


def test_platform_proxy_soundcloud():
    s = _settings(proxy_url="socks5h://base:1080", proxy_url_soundcloud="socks5h://sc:1080")
    assert _platform_proxy(s, "https://soundcloud.com/artist/track") == "socks5h://sc:1080"
    assert _platform_proxy(s, "https://api.soundcloud.com/tracks/1") == "socks5h://sc:1080"
    # No SoundCloud override -> falls back to the base proxy, then to none.
    assert _platform_proxy(_settings(proxy_url="socks5h://base:1080"), "https://soundcloud.com/a/b") == "socks5h://base:1080"
    assert _platform_proxy(_settings(), "https://soundcloud.com/a/b") == ""


def test_extra_args_uses_platform_proxy(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(
        _settings(proxy_url="socks5h://base:1080", proxy_url_x="socks5h://x:1080")
    )
    args = runner._extra_args(playlist=False, playlist_items=None, url="https://x.com/u/status/1")
    assert args[args.index("--proxy") + 1] == "socks5h://x:1080"


def test_extra_args_explicit_proxy_overrides_host(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings(proxy_url="socks5h://base:1080", proxy_url_x="socks5h://x:1080"))
    args = runner._extra_args(
        playlist=False,
        playlist_items=None,
        url="ytsearch5:query",  # no host -> would otherwise use the base proxy
        proxy="socks5h://explicit:9",
    )
    assert args[args.index("--proxy") + 1] == "socks5h://explicit:9"


def test_extra_args_empty_explicit_proxy_disables_proxy(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings(proxy_url="socks5h://base:1080"))
    args = runner._extra_args(
        playlist=False, playlist_items=None, url="https://soundcloud.com/a/b", proxy=""
    )
    assert "--proxy" not in args


def test_force_ipv6_only_for_youtube(monkeypatch):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings(force_ipv6=True))
    yt = runner._extra_args(playlist=False, playlist_items=None, url="https://youtu.be/abc")
    other = runner._extra_args(playlist=False, playlist_items=None, url="https://vimeo.com/1")
    assert "--force-ipv6" in yt
    assert "--force-ipv6" not in other


def test_extra_args_ignores_missing_cookies(monkeypatch, tmp_path):
    _no_js(monkeypatch)
    runner = YtDlpRunner(_settings(cookies_file=str(tmp_path / "nope.txt")))
    args = runner._extra_args(playlist=False, playlist_items=None)
    assert "--cookies" not in args


def test_extra_args_stages_cookies_into_private_subdir(monkeypatch, tmp_path):
    _no_js(monkeypatch)
    source = tmp_path / "master_cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n")
    runner = YtDlpRunner(_settings(cookies_file=str(source)))
    jobdir = tmp_path / "job"
    jobdir.mkdir()

    args = runner._extra_args(playlist=False, playlist_items=None, cookiejar_dir=jobdir)

    staged = Path(args[args.index("--cookies") + 1])
    assert staged != source
    assert staged.parent.name == ".cookies"
    assert staged.read_text() == source.read_text()
    # The staged copy must not look like downloadable media.
    assert staged.suffix == ".txt"


def test_extra_args_cookies_without_dir_uses_source(monkeypatch, tmp_path):
    _no_js(monkeypatch)
    source = tmp_path / "master_cookies.txt"
    source.write_text("# Netscape HTTP Cookie File\n")
    runner = YtDlpRunner(_settings(cookies_file=str(source)))
    args = runner._extra_args(playlist=False, playlist_items=None)
    assert args[args.index("--cookies") + 1] == str(source)


def test_is_youtube_url():
    assert _is_youtube_url("https://youtu.be/x")
    assert _is_youtube_url("https://www.youtube.com/watch?v=x")
    assert _is_youtube_url("https://m.youtube.com/watch?v=x")
    assert not _is_youtube_url("https://vimeo.com/1")
    assert not _is_youtube_url("https://notyoutube.com/1")
    assert not _is_youtube_url(None)


def test_detect_js_runtime_auto_none(monkeypatch):
    _no_js(monkeypatch)
    assert detect_js_runtime(_settings()) is None


def test_detect_js_runtime_explicit_path_passthrough(monkeypatch):
    _no_js(monkeypatch)
    assert detect_js_runtime(_settings(js_runtime="/custom/deno")) is None
    assert detect_js_runtime(_settings(js_runtime="deno:/custom/deno")) == "deno:/custom/deno"


def test_detect_js_runtime_skips_old_node(monkeypatch):
    monkeypatch.setattr(dl.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(dl, "_node_version", lambda: "18.19.0")
    assert detect_js_runtime(_settings()) is None


def test_detect_js_runtime_accepts_new_node(monkeypatch):
    monkeypatch.setattr(dl.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(dl, "_node_version", lambda: "22.1.0")
    assert detect_js_runtime(_settings()) == "node"


def test_mask_proxy():
    assert mask_proxy("") == "off"
    assert mask_proxy("socks5h://user:secret@100.64.0.5:1080") == "socks5h://***@100.64.0.5:1080"
    assert mask_proxy("http://1.2.3.4:8080") == "http://1.2.3.4:8080"
    assert "secret" not in mask_proxy("http://u:secret@h.example:1")


def test_mask_ip():
    assert mask_ip("203.0.113.42") == "203.0.*.*"
    assert mask_ip("  198.51.100.7  ") == "198.51.*.*"
    assert mask_ip("2001:db8::1") == "2001:db8:*"
    assert mask_ip("") == "unknown"
    assert mask_ip(None) == "unknown"
    assert mask_ip("not-an-ip") == "not-an-ip"


def test_format_proxy_summary():
    assert format_proxy_summary(_settings()) == "off"
    s = _settings(proxy_url="socks5h://base:1080", proxy_url_tiktok="socks5h://tt:1080")
    out = format_proxy_summary(s)
    assert out.startswith("socks5h://base:1080")
    assert "tt=socks5h://tt:1080" in out
    assert "sc=socks5h://sc:1080" in format_proxy_summary(
        _settings(proxy_url="socks5h://base:1080", proxy_url_soundcloud="socks5h://sc:1080")
    )
    assert "sp=socks5h://sp:1080" in format_proxy_summary(
        _settings(proxy_url="socks5h://base:1080", proxy_url_spotify="socks5h://sp:1080")
    )

async def test_probe_proxy_egress_off():
    probe = await YtDlpRunner(_settings()).probe_proxy_egress()
    assert not probe.ok
    assert "off" in probe.detail


async def test_probe_proxy_egress_ok_and_cached(monkeypatch):
    calls = {"n": 0}

    def fake_exit_ip(proxy):
        calls["n"] += 1
        return "203.0.113.42\n"

    monkeypatch.setattr(dl, "_exit_ip", fake_exit_ip)
    runner = YtDlpRunner(_settings(proxy_url="socks5h://127.0.0.1:1080"))
    probe = await runner.probe_proxy_egress()
    assert probe.ok and probe.detail == "203.0.*.*"
    await runner.probe_proxy_egress()
    assert calls["n"] == 1


async def test_probe_proxy_egress_failure_logs_once(monkeypatch, caplog):
    def boom(url, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(dl, "_exit_ip", boom)
    monkeypatch.setattr(dl, "_proxy_warned", [False])
    runner = YtDlpRunner(_settings(proxy_url="socks5h://127.0.0.1:1080"))
    with caplog.at_level("WARNING"):
        first = await runner.probe_proxy_egress()
        runner._proxy_exit_cache = None
        second = await runner.probe_proxy_egress()
    assert not first.ok and not second.ok
    assert sum("proxy egress check failed" in r.message for r in caplog.records) == 1


def test_curl_cffi_version_is_str():
    assert isinstance(curl_cffi_version(), str)


def test_classify_egress_error():
    assert "bot-wall" in classify_egress_error("ERROR: Sign in to confirm you're not a bot")
    assert classify_egress_error("operation timed out") == "timeout"
    assert classify_egress_error("ERROR: Unable to download webpage") == "network"
    assert classify_egress_error("no video formats found") == "no-formats"
    assert classify_egress_error("something else") == "error"


class _FakeProc:
    def __init__(self, out: bytes = b"", err: bytes = b"", rc: int = 0) -> None:
        self._out = out
        self._err = err
        self.returncode = rc

    async def communicate(self):
        return self._out, self._err

    def kill(self):  # pragma: no cover
        pass

    async def wait(self):
        return self.returncode


async def test_probe_egress_ok(monkeypatch):
    captured: dict[str, tuple] = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc(out=b"Me at the zoo\n", rc=0)

    monkeypatch.setattr(dl.asyncio, "create_subprocess_exec", fake_exec)
    probe = await YtDlpRunner(_settings()).probe_egress("https://www.youtube.com/watch?v=x")
    assert probe.ok and probe.detail == "Me at the zoo"
    assert "--skip-download" in captured["args"]


async def test_probe_egress_bot_wall(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(err=b"ERROR: Sign in to confirm you're not a bot", rc=1)

    monkeypatch.setattr(dl.asyncio, "create_subprocess_exec", fake_exec)
    probe = await YtDlpRunner(_settings()).probe_egress("https://www.youtube.com/watch?v=x")
    assert not probe.ok
    assert "bot-wall" in probe.detail


async def test_probe_egress_no_url(monkeypatch):
    probe = await YtDlpRunner(_settings(egress_test_url="")).probe_egress("")
    assert not probe.ok


async def test_probe_egress_caches_result(monkeypatch):
    calls = {"n": 0}

    async def fake_exec(*args, **kwargs):
        calls["n"] += 1
        return _FakeProc(out=b"Me at the zoo\n", rc=0)

    monkeypatch.setattr(dl.asyncio, "create_subprocess_exec", fake_exec)
    runner = YtDlpRunner(_settings())
    await runner.probe_egress("https://www.youtube.com/watch?v=x")
    await runner.probe_egress("https://www.youtube.com/watch?v=x")
    assert calls["n"] == 1
