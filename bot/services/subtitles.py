"""Subtitles: discovery, cleanup, burning and embedding.

Everything here is either a pure function (language discovery, SRT/VTT cleanup,
ffmpeg argument building) or a thin async subprocess wrapper, so it can be unit
tested without yt-dlp or ffmpeg. The bot only ever asks yt-dlp for the exact
track the user picked; burning/embedding then happens locally with ffmpeg.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.config import Settings

log = logging.getLogger(__name__)

BURN = "burn"
EMBED = "embed"
SRT = "srt"

# Extensions yt-dlp can leave behind for a subtitle track.
SUBTITLE_SUFFIXES = (".srt", ".vtt", ".ass", ".ssa", ".ttml", ".json3")

ProgressCallback = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class SubtitleOption:
    """One selectable subtitle language shown to the user."""

    key: str  # exact yt-dlp --sub-langs key, e.g. "en" or "fa-en"
    code: str  # base language code, e.g. "fa"
    kind: str  # "manual" | "auto" | "translated"
    name: str  # human-readable language name


# Common language names for the keyboard. Unknown codes fall back to the raw
# (upper-cased) code — the list never needs to be exhaustive.
_LANG_NAMES = {
    "en": "English",
    "de": "Deutsch",
    "fa": "فارسی",
    "ar": "العربية",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "pt": "Português",
    "ru": "Русский",
    "tr": "Türkçe",
    "zh": "中文",
    "zh-hans": "中文(简)",
    "zh-hant": "中文(繁)",
    "ja": "日本語",
    "ko": "한국어",
    "hi": "हिन्दी",
    "ur": "اردو",
    "nl": "Nederlands",
    "pl": "Polski",
    "uk": "Українська",
    "id": "Indonesia",
    "vi": "Tiếng Việt",
    "th": "ไทย",
    "he": "עברית",
    "sv": "Svenska",
    "no": "Norsk",
    "da": "Dansk",
    "fi": "Suomi",
    "cs": "Čeština",
    "el": "Ελληνικά",
    "ro": "Română",
    "hu": "Magyar",
    "bg": "Български",
    "az": "Azərbaycan",
    "ku": "Kurdî",
    "ps": "پښتو",
}

# ISO 639-1 -> ISO 639-2/B, required for the mp4 mov_text language tag.
_ISO639_2 = {
    "en": "eng", "de": "deu", "fa": "fas", "ar": "ara", "es": "spa", "fr": "fra",
    "it": "ita", "pt": "por", "ru": "rus", "tr": "tur", "zh": "zho", "ja": "jpn",
    "ko": "kor", "hi": "hin", "ur": "urd", "nl": "nld", "pl": "pol", "uk": "ukr",
    "he": "heb", "sv": "swe", "no": "nor", "da": "dan", "fi": "fin", "cs": "ces",
    "el": "ell", "ro": "ron", "hu": "hun", "bg": "bul", "az": "aze", "ku": "kur",
    "ps": "pus", "id": "ind", "vi": "vie", "th": "tha",
}


def _base(code: str) -> str:
    return code.split("-")[0].strip().lower()


def language_name(code: str) -> str:
    return _LANG_NAMES.get(code.lower()) or _LANG_NAMES.get(_base(code)) or code.upper()


def iso639_2(code: str | None) -> str:
    base = _base(code or "")
    return _ISO639_2.get(base, base[:3] or "und")


# --------------------------------------------------------------------------
# Language discovery
# --------------------------------------------------------------------------

def detect_source_language(info: dict[str, Any], auto: dict[str, Any]) -> str | None:
    """Best-effort original/spoken language of a video.

    Preference order: an explicit ``<lang>-orig`` automatic caption, then
    yt-dlp's ``info["language"]``, then a bare automatic-caption key, then the
    ``-<src>`` suffix shared by most translated keys.
    """
    for key in auto:
        if key.endswith("-orig") and len(key) > len("-orig"):
            return _base(key)
    lang = info.get("language")
    if isinstance(lang, str) and lang.strip():
        return _base(lang)
    bare = [k for k in auto if "-" not in k]
    if bare:
        return _base(bare[0])
    suffixes: dict[str, int] = {}
    for key in auto:
        if "-" in key:
            src = _base(key.split("-", 1)[1])
            if src:
                suffixes[src] = suffixes.get(src, 0) + 1
    if suffixes:
        return max(suffixes, key=lambda k: suffixes[k])
    return None


def _source_auto_key(auto: dict[str, Any], source_lang: str | None) -> str | None:
    if not source_lang:
        return None
    orig = f"{source_lang}-orig"
    if orig in auto:
        return orig
    if source_lang in auto:
        return source_lang
    return None


def _translated_key(auto: dict[str, Any], bot_lang: str, source_lang: str | None) -> str | None:
    if not bot_lang or bot_lang == source_lang:
        return None
    # A bare key would be the ASR track of that language (i.e. the source), so
    # only accept the explicit "<lang>-<source>" translated form.
    prefix = f"{bot_lang}-"
    for key in auto:
        if key.startswith(prefix) and len(key) > len(prefix):
            return key
    return None


def available_subtitle_options(info: dict[str, Any], bot_lang: str) -> list[SubtitleOption]:
    """Ordered subtitle choices: manual tracks, then the two auto options.

    Only the exact auto-translated key for ``bot_lang`` is offered (never the
    full translated list), and never when it would duplicate the ``(auto)``
    source entry.
    """
    manual_raw = info.get("subtitles") or {}
    auto_raw = info.get("automatic_captions") or {}
    source_lang = detect_source_language(info, auto_raw)

    manual: list[SubtitleOption] = []
    for key in manual_raw:
        if not key or key.endswith("-orig"):
            continue
        manual.append(
            SubtitleOption(key=key, code=_base(key), kind="manual", name=language_name(key))
        )

    def _rank(option: SubtitleOption) -> tuple[int, str]:
        if option.code == bot_lang:
            prio = 0
        elif source_lang and option.code == source_lang:
            prio = 1
        elif option.code == "en":
            prio = 2
        else:
            prio = 3
        return (prio, option.name.lower())

    manual.sort(key=_rank)
    options = list(manual)

    src_key = _source_auto_key(auto_raw, source_lang)
    if src_key:
        code = source_lang or _base(src_key)
        options.append(SubtitleOption(key=src_key, code=code, kind="auto", name=language_name(code)))

    tr_key = _translated_key(auto_raw, bot_lang, source_lang)
    if tr_key:
        options.append(
            SubtitleOption(key=tr_key, code=_base(bot_lang), kind="translated", name=language_name(bot_lang))
        )
    return options


# --------------------------------------------------------------------------
# SRT / VTT cleanup
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]*>")
_ASS_RE = re.compile(r"\{\\[^}]*\}")
_ENTITIES = (
    ("&nbsp;", " "),
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
)


def _clean_text(raw: str) -> str:
    text = _TAG_RE.sub("", raw)
    text = _ASS_RE.sub("", text)
    for needle, replacement in _ENTITIES:
        text = text.replace(needle, replacement)
    return text.replace("\u200b", "").strip()


def _norm_timestamp(raw: str) -> str | None:
    """Normalise a VTT/SRT timestamp to ``HH:MM:SS,mmm`` (SRT form)."""
    value = raw.strip().replace(".", ",")
    if not value:
        return None
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, rest = parts
    elif len(parts) == 2:
        hours, minutes, rest = "0", parts[0], parts[1]
    else:
        return None
    sec, _, millis = rest.partition(",")
    millis = (millis + "000")[:3]
    try:
        h, m, s, ms = int(hours), int(minutes), int(sec), int(millis)
    except ValueError:
        return None
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_cues(data: str) -> list[tuple[str, str, str]]:
    """Parse SRT/VTT text into ``(start, end, text)`` cues."""
    cleaned = (data or "").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[tuple[str, str, str]] = []
    for block in re.split(r"\n[ \t]*\n", cleaned):
        lines = [
            line.strip()
            for line in block.splitlines()
            if line.strip() and not line.strip().startswith(("WEBVTT", "NOTE", "STYLE", "REGION"))
        ]
        timing_idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_idx is None:
            continue
        start_raw, _, end_raw = lines[timing_idx].partition("-->")
        # VTT cue settings (align/position/…) follow the end timestamp.
        end_ts = end_raw.strip().split(" ", 1)[0]
        start = _norm_timestamp(start_raw)
        end = _norm_timestamp(end_ts)
        if start is None or end is None:
            continue
        body = _clean_text(" ".join(lines[timing_idx + 1 :]))
        if body:
            cues.append((start, end, body))
    return cues


def cues_to_srt(cues: list[tuple[str, str, str]]) -> str:
    """Render cues as SRT, dropping exact/rolling duplicates from auto captions."""
    blocks: list[str] = []
    previous = ""
    index = 0
    for start, end, raw in cues:
        text = raw
        if text == previous:
            continue
        if previous and text.startswith(previous):
            text = text[len(previous) :].strip()
        if not text:
            continue
        index += 1
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
        previous = raw
    return "\n".join(blocks)


def clean_subtitle_text(data: str) -> str:
    """Normalise an SRT/VTT blob to deduplicated SRT text."""
    if not data:
        return ""
    return cues_to_srt(parse_cues(data))


def _ts_to_seconds(raw: str) -> float | None:
    value = (raw or "").strip().replace(",", ".")
    if not value:
        return None
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, rest = parts
    elif len(parts) == 2:
        hours, minutes, rest = "0", parts[0], parts[1]
    else:
        return None
    try:
        return int(hours) * 3600 + int(minutes) * 60 + float(rest)
    except ValueError:
        return None


def _seconds_to_ts(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def slice_srt(data: str, start_s: float, dur_s: float, *, offset_s: float | None = None) -> str:
    """Keep cues overlapping ``[start_s, start_s+dur_s]`` and rebase their times.

    ``offset_s`` is the amount subtracted from every cue time so the result lines
    up with the produced clip's timeline. It defaults to ``start_s`` (the clip is
    assumed to start at timestamp 0); pass the clip's real first PTS otherwise.
    """
    if dur_s <= 0:
        return ""
    offset = start_s if offset_s is None else offset_s
    end_limit = start_s + dur_s
    kept: list[tuple[str, str, str]] = []
    for begin, end, text in parse_cues(data):
        b = _ts_to_seconds(begin)
        e = _ts_to_seconds(end)
        if b is None or e is None:
            continue
        if e <= start_s or b >= end_limit:
            continue
        shifted_b = max(0.0, b - offset)
        shifted_e = max(shifted_b, e - offset)
        kept.append((_seconds_to_ts(shifted_b), _seconds_to_ts(shifted_e), text))
    return cues_to_srt(kept)


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------

def escape_ffmpeg_filter_path(path: str) -> str:
    """Escape a filesystem path for use inside an ffmpeg filter argument.

    ``subtitles=filename='…'`` is parsed twice (filtergraph, then the quoted
    option), so filter-special characters must be backslash-escaped.
    """
    escaped = path.replace("\\", "\\\\")
    for ch in ("'", ":", ",", "[", "]", ";", "="):
        escaped = escaped.replace(ch, "\\" + ch)
    return escaped


def force_style(settings: Settings) -> str:
    """libass ``force_style`` string for burned-in subtitles."""
    return (
        f"FontName={settings.subtitle_font}"
        f",FontSize={settings.subtitle_font_size}"
        f",Outline={settings.subtitle_outline}"
        f",Shadow=0"
        f",MarginV={settings.subtitle_margin_v}"
    )


def build_burn_args(
    video: Path,
    srt: Path,
    out: Path,
    settings: Settings,
    *,
    bitrate_kbps: int | None = None,
    seek_s: float = 0.0,
    duration_s: float = 0.0,
) -> list[str]:
    """Assemble the ffmpeg argument list to burn ``srt`` into ``video``.

    ``seek_s`` seeks the input (fast, resets output time to zero, so the SRT must
    be rebased to match) and ``duration_s`` trims the output; both are used when
    burning a window out of a longer download.
    """
    style = force_style(settings)
    escaped = escape_ffmpeg_filter_path(str(srt))
    vf = f"subtitles=filename='{escaped}':force_style='{style}'"
    args = ["-y"]
    if seek_s > 0:
        args += ["-ss", f"{seek_s:.3f}"]
    args += ["-i", str(video), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast"]
    if bitrate_kbps:
        args += ["-b:v", f"{int(bitrate_kbps)}k"]
    else:
        args += ["-crf", "23"]
    args += ["-threads", str(max(1, settings.subtitle_threads))]
    # A seek resets timestamps, so copy would risk A/V drift; re-encode instead.
    args += ["-c:a", "aac" if seek_s > 0 else "copy"]
    if duration_s > 0:
        args += ["-t", f"{duration_s:.3f}"]
    args += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(out)]
    return args


def build_embed_args(video: Path, srt: Path, out: Path, *, language: str | None = None) -> list[str]:
    """Assemble the ffmpeg argument list to embed ``srt`` as a soft track."""
    args = [
        "-y",
        "-i",
        str(video),
        "-i",
        str(srt),
        "-map",
        "0",
        "-map",
        "1",
        "-c",
        "copy",
        "-c:s",
        "mov_text",
    ]
    if language:
        args += ["-metadata:s:s:0", f"language={iso639_2(language)}"]
    args += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(out)]
    return args


def target_bitrate_kbps(duration_s: float, limit_bytes: int, *, audio_kbps: int = 128) -> int | None:
    """Video bitrate that keeps a re-encode under ``limit_bytes``.

    Returns ``None`` when the budget is too small to be worth re-encoding.
    """
    if duration_s <= 0 or limit_bytes <= 0:
        return None
    total_kbps = (limit_bytes * 8 / duration_s) / 1000
    video_kbps = int(total_kbps - audio_kbps)
    return video_kbps if video_kbps >= 100 else None


def collect_subtitle_files(tmpdir: Path) -> list[Path]:
    """Subtitle files produced by yt-dlp, ``.srt`` first."""
    if not tmpdir.exists():
        return []
    files = [
        p
        for p in tmpdir.iterdir()
        if p.is_file() and p.suffix.lower() in {".srt", ".vtt"}
    ]
    return sorted(files, key=lambda p: (p.suffix.lower() != ".srt", p.name))


async def _run_ffmpeg_progress(
    args: list[str],
    *,
    timeout: int,
    duration_s: float = 0.0,
    on_progress: ProgressCallback | None = None,
) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def _consume() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            if on_progress and duration_s > 0:
                match = re.search(rb"out_time_ms=(\d+)", line)
                if match:
                    seconds = int(match.group(1)) / 1_000_000
                    await on_progress(min(99.0, seconds / duration_s * 100))
        await proc.wait()

    try:
        await asyncio.wait_for(_consume(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(OSError):
            await proc.wait()
        return False
    return proc.returncode in (0, None)


async def burn_subtitles(
    video: Path,
    srt: Path,
    settings: Settings,
    *,
    duration_s: float = 0.0,
    bitrate_kbps: int | None = None,
    on_progress: ProgressCallback | None = None,
    timeout: int = 1800,
) -> bool:
    """Burn ``srt`` into ``video`` in place. Returns ``True`` on success."""
    out = video.with_name(f"{video.stem}.burned{video.suffix}")
    args = build_burn_args(video, srt, out, settings, bitrate_kbps=bitrate_kbps)
    ok = await _run_ffmpeg_progress(
        args, timeout=timeout, duration_s=duration_s, on_progress=on_progress
    )
    return _replace_with(video, out, ok)


async def burn_segment(
    video: Path,
    srt: Path,
    settings: Settings,
    *,
    start_s: float,
    duration_s: float,
    seek_s: float = 0.0,
    offset_s: float | None = None,
    bitrate_kbps: int | None = None,
    on_progress: ProgressCallback | None = None,
    timeout: int = 1800,
) -> bool:
    """Burn the ``[start_s, start_s+duration_s]`` window of ``video`` in place.

    The SRT is sliced to the window (``offset_s`` rebases cue times onto the clip
    timeline); ``seek_s`` trims a full-length file, or stays 0 when the file is
    already just the window.
    """
    try:
        raw = srt.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    sliced = slice_srt(raw, start_s, duration_s, offset_s=offset_s)
    if not sliced.strip():
        return False
    clip_srt = srt.with_name(f"{srt.stem}.clip.srt")
    try:
        clip_srt.write_text(sliced, encoding="utf-8")
    except OSError:
        return False
    out = video.with_name(f"{video.stem}.burned{video.suffix}")
    args = build_burn_args(
        video, clip_srt, out, settings, bitrate_kbps=bitrate_kbps, seek_s=seek_s, duration_s=duration_s
    )
    ok = await _run_ffmpeg_progress(
        args, timeout=timeout, duration_s=duration_s, on_progress=on_progress
    )
    _remove(clip_srt)
    return _replace_with(video, out, ok)


async def probe_start_time(path: Path) -> float:
    """Best-effort first timestamp of ``path`` via ffprobe (off the event loop)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    import subprocess

    def _run() -> float:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=start_time", "-of",
                 "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return float(result.stdout.strip())
        except (ValueError, subprocess.SubprocessError, OSError):
            return 0.0

    return await asyncio.to_thread(_run)


async def embed_subtitles(
    video: Path,
    srt: Path,
    *,
    language: str | None = None,
    timeout: int = 300,
) -> bool:
    """Embed ``srt`` as a soft ``mov_text`` track in an mp4 video, in place."""
    if video.suffix.lower() != ".mp4":
        return False
    out = video.with_name(f"{video.stem}.embedded{video.suffix}")
    args = build_embed_args(video, srt, out, language=language)
    ok = await _run_ffmpeg_progress(args, timeout=timeout)
    if not ok or not out.exists() or out.stat().st_size == 0:
        _remove(out)
        return False
    try:
        video.unlink()
        out.replace(video)
    except OSError:
        _remove(out)
        return False
    return True


def _replace_with(video: Path, out: Path, ok: bool) -> bool:
    """Replace ``video`` with a successfully produced ``out`` file."""
    if not ok or not out.exists() or out.stat().st_size == 0:
        _remove(out)
        return False
    try:
        video.unlink()
        out.replace(video)
    except OSError:
        _remove(out)
        return False
    return True


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
