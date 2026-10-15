from __future__ import annotations

from pathlib import Path

from bot.services import tagger


def test_sanitize_tag_strips_control_chars():
    assert tagger.sanitize_tag("a\nb\rc\x00d") == "a b c d"
    assert tagger.sanitize_tag("  spaced  ") == "spaced"
    assert tagger.sanitize_tag(None) == ""
    assert len(tagger.sanitize_tag("x" * 500)) == 200


def test_build_args_mp3_with_cover(tmp_path):
    audio = tmp_path / "song.mp3"
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"j")
    args = tagger.build_tag_args(audio, tmp_path / "out.mp3", title="T", artist="A", cover=cover)
    assert "-map" in args and "1:v" in args
    assert "-id3v2_version" in args
    assert "-disposition:v" not in args  # m4a-only flag
    assert "title=T" in args and "artist=A" in args


def test_build_args_m4a_with_cover(tmp_path):
    audio = tmp_path / "song.m4a"
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"j")
    args = tagger.build_tag_args(audio, tmp_path / "out.m4a", title="T", artist="A", cover=cover)
    assert args[args.index("-disposition:v") + 1] == "attached_pic"
    assert "-id3v2_version" not in args


def test_build_args_without_cover(tmp_path):
    args = tagger.build_tag_args(tmp_path / "song.mp3", tmp_path / "out.mp3", title="T", artist="", cover=None)
    assert "1:v" not in args
    assert "title=T" in args
    assert "artist=" not in args


async def test_tag_audio_success(tmp_path):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"original")

    def fake_run(args: list[str], timeout: int) -> bool:
        # Emulate ffmpeg writing the output file.
        Path(args[-1]).write_bytes(b"tagged-bytes")
        return True

    ok = await tagger.tag_audio(src, title="T", artist="A", runner=fake_run)
    assert ok is True
    assert src.read_bytes() == b"tagged-bytes"
    assert not (tmp_path / "song.tagged.mp3").exists()


async def test_tag_audio_failure_keeps_original(tmp_path):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"original")

    def failing_run(args: list[str], timeout: int) -> bool:
        return False

    ok = await tagger.tag_audio(src, title="T", runner=failing_run)
    assert ok is False
    assert src.read_bytes() == b"original"


async def test_tag_audio_unsupported_suffix(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    assert await tagger.tag_audio(src, title="T") is False


async def test_tag_audio_requires_something_to_write(tmp_path):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"x")
    assert await tagger.tag_audio(src) is False
