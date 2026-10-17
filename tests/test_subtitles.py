from __future__ import annotations

from pathlib import Path

from bot.config import Settings
from bot.services import subtitles as sub


def _settings(**kwargs) -> Settings:
    base = {"_env_file": None, "bot_token": "x"}
    base.update(kwargs)
    return Settings(**base)


# --------------------------------------------------------------------------
# Language discovery
# --------------------------------------------------------------------------

def test_detect_source_language_prefers_orig_key():
    assert sub.detect_source_language({"language": "de"}, {"en-orig": []}) == "en"


def test_detect_source_language_uses_info_language():
    assert sub.detect_source_language({"language": "de"}, {"fr-en": []}) == "de"


def test_detect_source_language_bare_key():
    assert sub.detect_source_language({}, {"fr": []}) == "fr"


def test_detect_source_language_from_suffix_majority():
    auto = {"fa-en": [], "de-en": [], "tr-en": []}
    assert sub.detect_source_language({}, auto) == "en"


def test_detect_source_language_none_without_captions():
    assert sub.detect_source_language({}, {}) is None


def test_available_options_manual_auto_and_translated():
    info = {
        "language": "en",
        "subtitles": {"en": [{}], "de": [{}]},
        "automatic_captions": {"en": [{}], "fa-en": [{}]},
    }
    options = sub.available_subtitle_options(info, "fa")
    kinds = [(o.kind, o.key) for o in options]
    assert ("manual", "en") in kinds
    assert ("manual", "de") in kinds
    assert ("auto", "en") in kinds
    assert ("translated", "fa-en") in kinds


def test_available_options_translated_not_duplicated_in_bot_language():
    info = {"language": "fa", "subtitles": {"fa": [{}]}, "automatic_captions": {"fa": [{}]}}
    options = sub.available_subtitle_options(info, "fa")
    assert all(o.kind != "translated" for o in options)


def test_available_options_only_translated_keys():
    info = {"automatic_captions": {"fa-en": [{}]}}
    options = sub.available_subtitle_options(info, "fa")
    assert len(options) == 1
    assert options[0].kind == "translated"
    assert options[0].key == "fa-en"


def test_available_options_empty_without_captions():
    assert sub.available_subtitle_options({}, "en") == []


def test_available_options_manual_sorted_bot_language_first():
    info = {"subtitles": {"zz": [{}], "fa": [{}], "en": [{}]}}
    options = sub.available_subtitle_options(info, "fa")
    assert [o.key for o in options] == ["fa", "en", "zz"]


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------

def test_clean_subtitle_text_converts_vtt_and_dedupes_rolling_lines():
    vtt = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000 align:start\n"
        "hello\n\n"
        "00:00:03.000 --> 00:00:05.000\n"
        "hello world\n\n"
        "00:00:05.000 --> 00:00:07.000\n"
        "hello world\n"
    )
    result = sub.clean_subtitle_text(vtt)
    assert "00:00:01,000 --> 00:00:03,000" in result
    assert result.count("hello") == 1
    assert "world" in result
    # Duplicate rolling cue must not produce a third block.
    assert result.count("-->") == 2


def test_clean_subtitle_text_strips_tags_and_entities():
    vtt = (
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n"
        "<c.color>Tom &amp; Jerry</c> <00:00:00.500>now\n"
    )
    result = sub.clean_subtitle_text(vtt)
    assert "Tom & Jerry now" in result
    assert "<" not in result and "{" not in result


def test_clean_subtitle_text_empty():
    assert sub.clean_subtitle_text("") == ""


def test_iso639_2_maps_and_falls_back():
    assert sub.iso639_2("fa") == "fas"
    assert sub.iso639_2("fa-en") == "fas"
    assert sub.iso639_2("xx") == "xx"
    assert sub.iso639_2(None) == "und"


# --------------------------------------------------------------------------
# ffmpeg arguments
# --------------------------------------------------------------------------

def test_build_burn_args_contains_filter_and_styling():
    args = sub.build_burn_args(Path("/tmp/v.mp4"), Path("/tmp/a:b.srt"), Path("/tmp/o.mp4"), _settings())
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("subtitles=filename=")
    assert "force_style=" in vf
    assert "-crf" in args
    assert args[args.index("-threads") + 1] == "2"
    assert "+faststart" in args
    # The path is escaped for the double-parsed filter argument.
    assert "a\\:b" in vf


def test_build_burn_args_uses_bitrate_when_given():
    args = sub.build_burn_args(Path("v.mp4"), Path("s.srt"), Path("o.mp4"), _settings(), bitrate_kbps=1500)
    assert "-b:v" in args
    assert "1500k" in args
    assert "-crf" not in args


def test_escape_ffmpeg_filter_path():
    escaped = sub.escape_ffmpeg_filter_path("/tmp/a:b'c,d.mp4")
    assert "\\:" in escaped
    assert "\\'" in escaped
    assert "\\," in escaped


def test_build_embed_args_maps_and_tags_language():
    args = sub.build_embed_args(Path("v.mp4"), Path("s.srt"), Path("o.mp4"), language="fa")
    assert "mov_text" in args
    assert "-metadata:s:s:0" in args
    assert args[args.index("-metadata:s:s:0") + 1] == "language=fas"
    assert "+faststart" in args


def test_target_bitrate_kbps():
    # 10 MiB over 100 s is ~838.9 kbps total; 128 kbps of audio leaves ~710.
    assert sub.target_bitrate_kbps(100, 10 * 1024 * 1024) == 710
    assert sub.target_bitrate_kbps(0, 10 * 1024 * 1024) is None
    assert sub.target_bitrate_kbps(100, 0) is None


def test_iso639_2_unknown_falls_back():
    assert sub.iso639_2("qq") == "qq"


def test_collect_subtitle_files_prefers_srt(tmp_path: Path):
    (tmp_path / "a.en.vtt").write_text("WEBVTT\n")
    (tmp_path / "a.en.srt").write_text("1\n")
    (tmp_path / "media.mp4").write_bytes(b"x")
    files = sub.collect_subtitle_files(tmp_path)
    assert [p.name for p in files] == ["a.en.srt", "a.en.vtt"]


def test_collect_subtitle_files_missing_dir(tmp_path: Path):
    assert sub.collect_subtitle_files(tmp_path / "nope") == []


def _find_arg(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args else None
