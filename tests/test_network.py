"""Optional integration tests that hit the real network (yt-dlp).

Enable with:  RUN_NETWORK_TESTS=1 pytest -m network
"""

from __future__ import annotations

import os

import pytest
from bot.config import Settings
from bot.services.downloader import DownloadJob, YtDlpRunner

pytestmark = pytest.mark.network

RUN = os.environ.get("RUN_NETWORK_TESTS") == "1"
TEST_URL = "https://www.youtube.com/watch?v=BaW_jenozKc"


@pytest.mark.skipif(not RUN, reason="set RUN_NETWORK_TESTS=1 to enable")
async def test_extract_info_real():
    runner = YtDlpRunner(Settings(_env_file=None, bot_token="x"))
    info = await runner.extract_info(TEST_URL)
    assert info.get("title")
    assert info.get("formats")


@pytest.mark.skipif(not RUN, reason="set RUN_NETWORK_TESTS=1 to enable")
async def test_download_thumbnail_real(tmp_path):
    runner = YtDlpRunner(Settings(_env_file=None, bot_token="x"))
    job = DownloadJob(url=TEST_URL, format_key="thumbnail", tmpdir=tmp_path, title_hint="t")
    path = await runner.download_thumbnail(job)
    assert path is not None and path.exists()
    assert path.stat().st_size > 0
