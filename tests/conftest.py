"""Shared pytest fixtures.

Everything is mocked: no network, no real yt-dlp/ffmpeg, no real Telegram. The
handler functions are invoked directly with lightweight fakes so the whole
orchestration path (pick -> queue -> download -> upload -> cache) is exercised.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from bot import deps
from bot.config import settings as real_settings
from bot.db import Database
from bot.deps import Services
from bot.services.cache import DownloadCache
from bot.services.download_links import DownloadLinkStore
from bot.services.queue import JobQueue
from bot.services.ratelimit import RateLimiter

ADMIN_ID = 999
USER_ID = 42


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeMessage:
    def __init__(self, user_id: int = USER_ID, text: str = "", chat_id: int | None = None) -> None:
        self.from_user = SimpleNamespace(
            id=user_id,
            first_name="Tester",
            full_name="Tester T.",
            username="tester",
            language_code="en",
        )
        self.text = text
        self.chat = SimpleNamespace(id=chat_id if chat_id is not None else user_id)
        self.message_id = 1
        self.sent: list[str] = []
        self.edits: list[str] = []
        self.markups: list[object] = []
        self.deleted = False

    async def answer(self, body, reply_markup=None, **kwargs):
        self.sent.append(body)
        self.markups.append(reply_markup)
        return self

    async def edit_text(self, body, reply_markup=None, **kwargs):
        self.edits.append(body)
        self.markups.append(reply_markup)

    async def delete(self) -> None:
        self.deleted = True


class FakeCallback:
    def __init__(self, user_id: int = USER_ID, data: str = "", message: FakeMessage | None = None) -> None:
        self.from_user = SimpleNamespace(id=user_id, language_code="en")
        self.data = data
        self.message = message or FakeMessage(user_id)
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, body="", show_alert=False, **kwargs) -> None:
        self.answers.append((body, show_alert))


class FakeRunner:
    def __init__(self) -> None:
        self.info = _default_info()
        self.info_error: Exception | None = None
        self.download_error: Exception | None = None
        self.media_size = 2048
        self.calls: list[object] = []
        self.info_calls: list[str] = []
        self.thumb_error = False

    async def extract_info(self, url, *, playlist=False, playlist_items=None, cookiejar_dir=None):
        self.info_calls.append(url)
        if self.info_error:
            raise self.info_error
        return self.info

    async def download(self, job, *, on_progress=None, start_callback=None, write_thumbnail=False):
        self.calls.append(job)
        if start_callback:
            await start_callback()
        if self.download_error:
            raise self.download_error
        if on_progress:
            await on_progress(50.0, 0, self.media_size)
        job.tmpdir.mkdir(parents=True, exist_ok=True)
        ext = "mp3" if job.format_key.startswith("audio") else "mp4"
        path = job.tmpdir / f"media.{ext}"
        path.write_bytes(b"\x00" * self.media_size)
        if write_thumbnail:
            (job.tmpdir / "media.jpg").write_bytes(b"\x00" * 128)
        return [path]

    async def download_thumbnail(self, job):
        if self.thumb_error:
            return None
        job.tmpdir.mkdir(parents=True, exist_ok=True)
        path = job.tmpdir / "media.jpg"
        path.write_bytes(b"\x00" * 128)
        return path

    async def probe_egress(self, url=None):
        return SimpleNamespace(ok=True, detail="Test Video")

    async def probe_proxy_egress(self):
        return SimpleNamespace(ok=False, detail="proxy off")


class FakeUpdater:
    def __init__(self) -> None:
        self.retry = False
        self.updates = 0

    def should_retry(self, error_text: str) -> bool:
        return self.retry

    async def update(self):
        self.updates += 1
        from bot.services.updater import UpdateResult

        return UpdateResult(ok=True, old_version="old", new_version="new", message="ok")


class FakeBot:
    def __init__(self) -> None:
        self.videos: list[tuple] = []
        self.audios: list[tuple] = []
        self.photos: list[tuple] = []
        self.media_groups: list[tuple] = []
        self.groups: list[object] = []
        self.messages: list[tuple[int, str]] = []
        self.raise_for: set[int] = set()
        self.session = SimpleNamespace(close=self._close)

    async def _close(self) -> None:  # pragma: no cover
        pass

    async def me(self):
        return SimpleNamespace(username="testbot", id=1)

    async def send_video(self, chat_id, file, **kwargs):
        if chat_id in self.raise_for:
            raise RuntimeError("nope")
        self.videos.append((chat_id, file, kwargs))
        return SimpleNamespace(video=SimpleNamespace(file_id=f"vid{len(self.videos)}"))

    async def send_audio(self, chat_id, file, **kwargs):
        if chat_id in self.raise_for:
            raise RuntimeError("nope")
        self.audios.append((chat_id, file, kwargs))
        return SimpleNamespace(audio=SimpleNamespace(file_id=f"aud{len(self.audios)}"))

    async def send_photo(self, chat_id, file, **kwargs):
        self.photos.append((chat_id, file, kwargs))
        return SimpleNamespace(message_id=1)

    async def send_media_group(self, chat_id, media, **kwargs):
        if chat_id in self.raise_for:
            raise RuntimeError("media group invalid")
        self.media_groups.append((chat_id, media))
        return []

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.raise_for:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        return SimpleNamespace(message_id=1)


class FakeUpdaterWithRetry(FakeUpdater):
    def __init__(self, retry: bool) -> None:
        super().__init__()
        self.retry = retry


def _default_info() -> dict:
    return {
        "id": "abc123",
        "title": "Test Video",
        "uploader": "Uploader",
        "duration": 120,
        "width": 1280,
        "height": 720,
        "extractor_key": "Youtube",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "formats": [
            {"format_id": "137", "height": 1080, "width": 1920, "vcodec": "avc1", "acodec": "none", "filesize": 40_000_000},
            {"format_id": "136", "height": 720, "width": 1280, "vcodec": "avc1", "acodec": "none", "filesize": 20_000_000},
            {"format_id": "135", "height": 480, "width": 854, "vcodec": "avc1", "acodec": "none", "filesize": 10_000_000},
            {"format_id": "140", "height": None, "vcodec": "none", "acodec": "mp4a", "filesize": 2_000_000},
        ],
    }


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Point the global settings singleton at throwaway paths for this test."""
    monkeypatch.setattr(real_settings, "admin_ids", [ADMIN_ID], raising=False)
    monkeypatch.setattr(real_settings, "max_upload_mb", 50, raising=False)
    monkeypatch.setattr(real_settings, "bot_api_url", "", raising=False)
    monkeypatch.setattr(real_settings, "temp_dir", str(tmp_path / "tmp"), raising=False)
    monkeypatch.setattr(real_settings, "db_path", str(tmp_path / "db.sqlite"), raising=False)
    monkeypatch.setattr(real_settings, "per_user_daily_limit", 30, raising=False)
    monkeypatch.setattr(real_settings, "cooldown_seconds", 3.0, raising=False)
    monkeypatch.setattr(real_settings, "max_duration_min", 60, raising=False)
    monkeypatch.setattr(real_settings, "playlist_max_items", 10, raising=False)
    monkeypatch.setattr(real_settings, "max_media_group", 10, raising=False)
    monkeypatch.setattr(real_settings, "local_api", False, raising=False)
    monkeypatch.setattr(real_settings, "default_language", "en", raising=False)
    monkeypatch.setattr(real_settings, "progress_edit_interval", 0.0, raising=False)
    monkeypatch.setattr(real_settings, "auto_update_ytdlp", False, raising=False)
    monkeypatch.setattr(real_settings, "proxy_url", "", raising=False)
    monkeypatch.setattr(real_settings, "cookies_file", "", raising=False)
    monkeypatch.setattr(real_settings, "ytdlp_extra_args", "", raising=False)
    monkeypatch.setattr(real_settings, "force_ipv4", False, raising=False)
    monkeypatch.setattr(real_settings, "force_ipv6", False, raising=False)
    monkeypatch.setattr(real_settings, "js_runtime", "auto", raising=False)
    monkeypatch.setattr(real_settings, "min_free_disk_gb", 0.0, raising=False)
    monkeypatch.setattr(real_settings, "download_link_base_url", "http://links.test", raising=False)
    monkeypatch.setattr(real_settings, "download_link_secret", "testsecret", raising=False)
    monkeypatch.setattr(real_settings, "download_link_dir", str(tmp_path / "links"), raising=False)
    monkeypatch.setattr(real_settings, "download_link_ttl_hours", 6, raising=False)
    monkeypatch.setattr(real_settings, "download_link_max_active", 3, raising=False)
    monkeypatch.setattr(real_settings, "download_link_max_total_gb", 20.0, raising=False)
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return real_settings


@pytest.fixture
async def db(settings):
    database = Database(settings.db_path)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def services(settings, db, runner, bot):
    srv = Services(
        settings=settings,
        db=db,
        runner=runner,
        queue=JobQueue(settings.max_jobs),
        ratelimiter=RateLimiter(db, settings),
        cache=DownloadCache(db, settings),
        updater=FakeUpdaterWithRetry(retry=False),
        download_links=DownloadLinkStore(settings),
        bot=bot,
    )
    deps._services = srv
    try:
        yield srv
    finally:
        deps._services = None


@pytest.fixture(autouse=True)
def _clear_pending():
    from bot.handlers import links

    links._pending.clear()
    yield
    links._pending.clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Neutralise SSRF/DNS checks in handler tests (unit-tested separately)."""
    from bot.handlers import inline, links

    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(links, "check_url_safety", _ok)
    monkeypatch.setattr(links, "validate_redirect_chain", _ok)
    monkeypatch.setattr(inline, "check_url_safety", _ok)


@pytest.fixture
def make_message():
    return FakeMessage


@pytest.fixture
def make_callback():
    return FakeCallback


@pytest.fixture
def media_file(tmp_path):
    def _make(name: str = "sample.mp4", size: int = 4096) -> Path:
        path = tmp_path / name
        path.write_bytes(b"\x00" * size)
        return path

    return _make
