from __future__ import annotations

import pytest
from bot.config import Settings
from bot.services.downloader import (
    DownloadError,
    DownloadJob,
    YtDlpRunner,
    _collect_media_files,
    _parse_size,
    best_estimate,
    disk_free_bytes,
    ensure_ffmpeg_available,
    estimate_format_size,
    video_quality_rows,
)


def _settings() -> Settings:
    return Settings(_env_file=None, bot_token="x")


def test_format_args_video():
    assert YtDlpRunner._format_args("video_720", 720, None) == [
        "-f",
        "bv*[height<=720]+ba/b[height<=720]",
    ]
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


def test_estimate_format_size_picks_height():
    assert estimate_format_size(INFO, 480) == 12_000_000
    assert estimate_format_size(INFO, 99999) is None


def test_best_estimate_uses_top_height():
    assert best_estimate(INFO) == 42_000_000


def test_helpers_on_real_paths(tmp_path):
    assert ensure_ffmpeg_available() in (True, False)
    assert disk_free_bytes(tmp_path) > 0
    job = DownloadJob(url="u", format_key="video_720", tmpdir=tmp_path)
    assert job.queue_position == 0
