"""Handler tests for the media converter (bot/handlers/convert.py)."""

import json
from types import SimpleNamespace

from bot.handlers import convert
from bot.services.converter import MediaInfo
from tests.conftest import USER_ID, FakeMessage


def _media_message(kind: str, file_id: str = "file123", size: int = 1000) -> FakeMessage:
    """Build a FakeMessage carrying a media attachment."""
    msg = FakeMessage()
    msg.document = None
    msg.video = None
    msg.audio = None
    msg.photo = None
    msg.voice = None
    msg.video_note = None
    if kind == "video":
        msg.video = SimpleNamespace(file_id=file_id, file_size=size)
    elif kind == "audio":
        msg.audio = SimpleNamespace(file_id=file_id, file_size=size)
    elif kind == "image":
        msg.photo = [SimpleNamespace(file_id=file_id, file_size=size)]
    elif kind == "document_video":
        msg.document = SimpleNamespace(
            file_id=file_id, file_name="clip.mp4", mime_type="video/mp4", file_size=size
        )
    elif kind == "document_audio":
        msg.document = SimpleNamespace(
            file_id=file_id, file_name="song.mp3", mime_type="audio/mpeg", file_size=size
        )
    elif kind == "document_image":
        msg.document = SimpleNamespace(
            file_id=file_id, file_name="pic.jpg", mime_type="image/jpeg", file_size=size
        )
    return msg


def _mock_probe(monkeypatch, kind: str = "video"):
    async def fake_probe(path):
        return MediaInfo(kind=kind, duration_s=60.0, size_bytes=1000)
    monkeypatch.setattr(convert.converter, "probe_media", fake_probe)


async def test_cmd_convert(services, bot, make_message):
    msg = make_message(text="/convert")
    await convert.cmd_convert(msg, bot)
    assert msg.sent and "Media converter" in msg.sent[0]


async def test_media_upload_unsupported(services, bot, make_message):
    msg = make_message(text="hello")
    await convert.on_media_upload(msg, bot)
    assert any("supported" in s.lower() for s in msg.sent)


async def test_media_upload_shows_auto_presets(services, bot, make_message, monkeypatch, settings):
    _mock_probe(monkeypatch, "video")
    msg = _media_message("video")
    await convert.on_media_upload(msg, bot)

    # Should show one-tap preset picker (not format+quality separately).
    assert msg.edits and "conversion preset" in msg.edits[-1].lower()
    markup = msg.markups[-1]
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "convert:auto:video:mp4:mp4_720" in cbs
    assert "convert:auto:video:mp4:mp4_480" in cbs

    # State stored.
    assert 42 in convert._convert_state
    state = convert._convert_state[42]
    assert state.kind == "video"


async def test_auto_convert_saves_and_converts(services, bot, make_message, monkeypatch, settings):
    _mock_probe(monkeypatch, "video")
    msg = _media_message("video")
    await convert.on_media_upload(msg, bot)
    state = convert._convert_state[42]
    state.status_message = msg

    # Mock convert_media to create an output file and report success.
    output_path = state.tmpdir / "converted.mp4"
    output_path.write_bytes(b"converted-video-bytes")

    async def fake_convert(job, *, semaphore=None, timeout=None, duration_s=0.0):
        output_path.write_bytes(b"converted-video-bytes")
        return True

    monkeypatch.setattr(convert.converter, "convert_media", fake_convert)

    cb_msg = make_message()
    cb = SimpleNamespace(
        data="convert:auto:video:mp4:mp4_720",
        from_user=SimpleNamespace(id=42, language_code="en"),
        message=cb_msg,
        answers=[],
    )
    async def answer(*a, **k):
        cb.answers.append(a)
    cb.answer = answer
    await convert.on_auto_convert(cb, bot)

    # A video file should have been sent.
    assert len(bot.videos) == 1
    assert bot.videos[0][0] == 42

    # Preset saved for future auto-convert.
    saved = await services.db.get_convert_preset(USER_ID)
    assert saved is not None
    parsed = json.loads(saved)
    assert parsed["kind"] == "video"
    assert parsed["container"] == "mp4"
    assert parsed["preset"] == "mp4_720"

    # State cleaned up.
    assert 42 not in convert._convert_state


async def test_auto_convert_with_saved_preset(services, bot, make_message, monkeypatch, settings):
    """User with a saved preset gets auto-conversion immediately."""
    await services.db.set_convert_preset(USER_ID, json.dumps({"kind": "video", "container": "mp4", "preset": "mp4_720"}))

    _mock_probe(monkeypatch, "video")
    msg = _media_message("video")

    # Mock convert_media to capture the job and create an output file.
    captured_job = None
    convert_called = False
    captured_content = None

    async def fake_convert(job, *, semaphore=None, timeout=None, duration_s=0.0):
        nonlocal captured_job, output_path, convert_called, captured_content
        convert_called = True
        captured_job = job
        output_path = job.output_path
        output_path.write_bytes(b"converted-video-bytes")
        captured_content = output_path.read_bytes()
        return True

    output_path = None
    monkeypatch.setattr(convert.converter, "convert_media", fake_convert)
    await convert.on_media_upload(msg, bot)

    # Auto-conversion starts immediately (no picker shown).
    assert convert_called, "convert_media was not called"
    assert len(bot.videos) == 1
    assert bot.videos[0][0] == 42

    # Correct preset was used.
    assert captured_job is not None
    assert captured_job.media_type == "video"
    assert captured_job.preset == "mp4_720"

    # Preset saved for future auto-convert.
    saved = await services.db.get_convert_preset(USER_ID)
    assert saved is not None
    parsed = json.loads(saved)
    assert parsed["kind"] == "video"
    assert parsed["container"] == "mp4"
    assert parsed["preset"] == "mp4_720"

    # Output file content was captured inside the mock.
    assert output_path is not None
    assert captured_content == b"converted-video-bytes"

    # State cleaned up after conversion.
    assert 42 not in convert._convert_state


async def test_convert_cancel_callback(services, bot, make_message, monkeypatch, settings):
    _mock_probe(monkeypatch, "video")
    msg = _media_message("video")
    await convert.on_media_upload(msg, bot)
    state = convert._convert_state[42]
    state.status_message = msg

    cb_msg = make_message()
    cb = SimpleNamespace(
        data="convert:cancel:video",
        from_user=SimpleNamespace(id=42, language_code="en"),
        message=cb_msg,
        answers=[],
    )
    async def answer(*a, **k):
        cb.answers.append(a)
    cb.answer = answer
    await convert.on_convert_cancel(cb, bot)

    assert any("cancelling" in s.lower() for s in cb_msg.edits)
    assert 42 not in convert._convert_state
