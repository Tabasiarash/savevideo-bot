"""Integration tests that hit the real network (yt-dlp).

Enable with:  RUN_NETWORK_TESTS=1 pytest -m network

The default fixture is a small, publicly hosted direct MP4 so the pipeline can
be exercised from datacenter IPs. The YouTube case only runs when COOKIES_FILE
is set, because YouTube requires authentication from most VPS networks.
"""

from __future__ import annotations

import os

import pytest
from bot.config import Settings
from bot.services.downloader import DownloadJob, YtDlpRunner

pytestmark = pytest.mark.network

RUN = os.environ.get("RUN_NETWORK_TESTS") == "1"
SKIP = pytest.mark.skipif(not RUN, reason="set RUN_NETWORK_TESTS=1 to enable")

DIRECT_VIDEO_URL = (
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/"
    "Big_Buck_Bunny_360_10s_1MB.mp4"
)
YOUTUBE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
COOKIES = os.environ.get("COOKIES_FILE") or ""

NEEDS_COOKIES = pytest.mark.skipif(
    not COOKIES,
    reason="set COOKIES_FILE to run YouTube tests from a datacenter IP",
)


@SKIP
async def test_extract_info_real():
    runner = YtDlpRunner(Settings(_env_file=None, bot_token="x"))
    info = await runner.extract_info(DIRECT_VIDEO_URL)
    assert info.get("title")
    assert info.get("formats")


@SKIP
async def test_download_video_real(tmp_path):
    runner = YtDlpRunner(Settings(_env_file=None, bot_token="x"))
    job = DownloadJob(
        url=DIRECT_VIDEO_URL, format_key="video_best", tmpdir=tmp_path, title_hint="bb"
    )
    paths = await runner.download(job)
    assert paths
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)


@SKIP
@NEEDS_COOKIES
async def test_youtube_extract_with_cookies():
    runner = YtDlpRunner(Settings(_env_file=None, bot_token="x", cookies_file=COOKIES))
    info = await runner.extract_info(YOUTUBE_URL)
    assert info.get("title")
    assert info.get("formats")
