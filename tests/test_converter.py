"""Unit tests for the media converter service (bot/services/converter.py)."""

from pathlib import Path

from bot.services.converter import (
    _AUDIO_PRESETS,
    _IMAGE_PRESETS,
    _VIDEO_PRESETS,
    ConvertJob,
    audio_presets,
    build_convert_args,
    image_presets,
    video_presets,
)


def test_build_convert_args_video_1080():
    job = ConvertJob(
        input_path=Path("in.mp4"),
        output_path=Path("out.mp4"),
        media_type="video",
        preset="mp4_1080",
    )
    args = build_convert_args(job)
    assert "-i" in args
    assert str(job.input_path) in args
    assert str(job.output_path) in args
    assert "-vf" in args and "scale=-2:1080" in args
    assert "-c:v" in args and "libx264" in args
    assert "-crf" in args and "23" in args
    assert "-c:a" in args and "aac" in args
    assert "-movflags" in args and "+faststart" in args


def test_build_convert_args_audio_only():
    job = ConvertJob(
        input_path=Path("in.mp4"),
        output_path=Path("out.mp3"),
        media_type="video",
        preset="mp4_audio",
    )
    args = build_convert_args(job)
    assert "-vn" in args
    assert "-c:a" in args and "libmp3lame" in args
    assert "-b:a" in args and "192k" in args


def test_build_convert_args_audio_mp3():
    job = ConvertJob(
        input_path=Path("in.wav"),
        output_path=Path("out.mp3"),
        media_type="audio",
        preset="mp3_128",
    )
    args = build_convert_args(job)
    assert "-vn" in args
    assert "-c:a" in args and "libmp3lame" in args
    assert "-b:a" in args and "128k" in args


def test_build_convert_args_image_jpg():
    job = ConvertJob(
        input_path=Path("in.png"),
        output_path=Path("out.jpg"),
        media_type="image",
        preset="jpg_60",
    )
    args = build_convert_args(job)
    assert "-vn" in args and "-an" in args
    assert "-c:v" in args and "mjpeg" in args
    assert "-q:v" in args and "60" in args


def test_build_convert_args_image_png_resize():
    job = ConvertJob(
        input_path=Path("in.png"),
        output_path=Path("out.png"),
        media_type="image",
        preset="png",
    )
    args = build_convert_args(job)
    assert "-c:v" in args and "png" in args
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("scale=")


def test_preset_labels_nonempty():
    assert video_presets()
    assert audio_presets()
    assert image_presets()
    for p in _VIDEO_PRESETS:
        assert p in video_presets()
    for p in _AUDIO_PRESETS:
        assert p in audio_presets()
    for p in _IMAGE_PRESETS:
        assert p in image_presets()


def test_unknown_preset_raises():
    job = ConvertJob(
        input_path=Path("in.mp4"),
        output_path=Path("out.mp4"),
        media_type="video",
        preset="nope",
    )
    try:
        build_convert_args(job)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown preset")
