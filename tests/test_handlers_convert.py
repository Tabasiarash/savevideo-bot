"""Handler tests for the media converter (bot/handlers/convert.py)."""

from types import SimpleNamespace

from bot.handlers import convert
from tests.conftest import FakeMessage


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


async def test_cmd_convert(services, bot, make_message):
    msg = make_message(text="/convert")
    await convert.cmd_convert(msg, bot)
    assert msg.sent and "Media converter" in msg.sent[0]


async def test_media_upload_unsupported(services, bot, make_message):
    msg = make_message(text="hello")
    await convert.on_media_upload(msg, bot)
    assert any("supported" in s.lower() for s in msg.sent)


async def test_media_upload_shows_format_keyboard(services, bot, make_message, monkeypatch, settings):
    msg = _media_message("video")
    await convert.on_media_upload(msg, bot)

    # Should offer format selection (edit replaces the fetching message).
    assert msg.edits and "Choose a target format" in msg.edits[-1]
    markup = msg.markups[-1]
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "convert:fmt:video:mp4" in cbs
    assert "convert:fmt:video:audio" in cbs

    # State stored.
    assert 42 in convert._convert_state
    state = convert._convert_state[42]
    assert state.kind == "video"


async def test_format_pick_shows_quality(services, bot, make_message, monkeypatch, settings):
    msg = _media_message("video")
    await convert.on_media_upload(msg, bot)
    state = convert._convert_state[42]
    state.status_message = msg

    cb_msg = make_message()
    cb = SimpleNamespace(
        data="convert:fmt:video:mp4",
        from_user=SimpleNamespace(id=42, language_code="en"),
        message=cb_msg,
        answers=[],
    )
    async def answer(*a, **k):
        cb.answers.append(a)
    cb.answer = answer
    await convert.on_format_pick(cb, bot)

    assert cb_msg.edits and "Choose a quality preset" in cb_msg.edits[-1]
    markup = cb_msg.markups[-1]
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "convert:qual:video:mp4:mp4_1080" in cbs
    assert state.container == "mp4"


async def test_quality_pick_shows_go(services, bot, make_message, monkeypatch, settings):
    msg = _media_message("video")
    await convert.on_media_upload(msg, bot)
    state = convert._convert_state[42]
    state.status_message = msg
    state.container = "mp4"

    cb_msg = make_message()
    cb = SimpleNamespace(
        data="convert:qual:video:mp4:mp4_720",
        from_user=SimpleNamespace(id=42, language_code="en"),
        message=cb_msg,
        answers=[],
    )
    async def answer(*a, **k):
        cb.answers.append(a)
    cb.answer = answer
    await convert.on_quality_pick(cb, bot)

    assert cb_msg.edits and "Convert" in cb_msg.edits[-1]
    markup = cb_msg.markups[-1]
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "convert:go:video:mp4:mp4_720" in cbs
    assert state.preset == "mp4_720"


async def test_convert_go_sends_file(services, bot, make_message, monkeypatch, settings):
    msg = _media_message("video")
    await convert.on_media_upload(msg, bot)
    state = convert._convert_state[42]
    state.status_message = msg
    state.container = "mp4"
    state.preset = "mp4_720"

    # Mock convert_media to create an output file and report success.
    output_path = state.tmpdir / "converted.mp4"
    output_path.write_bytes(b"converted-video-bytes")

    async def fake_convert(job, *, semaphore=None, timeout=None, duration_s=0.0):
        output_path.write_bytes(b"converted-video-bytes")
        return True

    monkeypatch.setattr(convert.converter, "convert_media", fake_convert)

    cb_msg = make_message()
    cb = SimpleNamespace(
        data="convert:go:video:mp4:mp4_720",
        from_user=SimpleNamespace(id=42, language_code="en"),
        message=cb_msg,
        answers=[],
    )
    async def answer(*a, **k):
        cb.answers.append(a)
    cb.answer = answer
    await convert.on_convert_go(cb, bot)

    # A video file should have been sent.
    assert len(bot.videos) == 1
    assert bot.videos[0][0] == 42
    # State cleaned up.
    assert 42 not in convert._convert_state


async def test_convert_go_cancelled(services, bot, make_message, monkeypatch, settings):
    msg = _media_message("video")
    await convert.on_media_upload(msg, bot)
    state = convert._convert_state[42]
    state.status_message = msg
    state.container = "mp4"
    state.preset = "mp4_720"
    state.cancelled = True

    # Mock convert_media to return failure quickly (no real ffmpeg).
    async def fake_convert(job, *, semaphore=None, timeout=None, duration_s=0.0):
        return False

    monkeypatch.setattr(convert.converter, "convert_media", fake_convert)

    cb_msg = make_message()
    cb = SimpleNamespace(
        data="convert:go:video:mp4:mp4_720",
        from_user=SimpleNamespace(id=42, language_code="en"),
        message=cb_msg,
        answers=[],
    )
    async def answer(*a, **k):
        cb.answers.append(a)
    cb.answer = answer
    await convert.on_convert_go(cb, bot)

    assert any("cancelled" in s.lower() for s in cb_msg.edits)
    assert 42 not in convert._convert_state


async def test_convert_cancel_callback(services, bot, make_message, monkeypatch, settings):
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
