"""Media converter: video / audio / image quality reduction.

Users send a media file and the bot converts it to a target format/quality.
If the user has a saved preset, conversion starts immediately (auto-convert).
Otherwise the user picks a preset in one tap, which is saved for next time.

Conversions run under a concurrency semaphore so ffmpeg does not overwhelm the
host, and each user can cancel an in-progress conversion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiofiles
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.deps import get_services
from bot.handlers import links
from bot.handlers.common import register_user, text
from bot.services import converter
from bot.services.downloader import cleanup_dir

log = logging.getLogger(__name__)

router = Router(name="convert")

# Callback prefixes.
_FMT_PREFIX = "convert:fmt"      # convert:fmt:<kind>:<container>
_QUAL_PREFIX = "convert:qual"    # convert:qual:<kind>:<container>:<preset>
_GO_PREFIX = "convert:go"        # convert:go:<kind>:<container>:<preset>
_AUTO_PREFIX = "convert:auto"    # convert:auto:<kind>:<container>:<preset>
_CANCEL_PREFIX = "convert:cancel"  # convert:cancel:<kind>
_CHANGE_PREFIX = "convert:change"  # convert:change:<kind>

# Mapping container -> preset keys offered on the quality step.
_FORMAT_PRESETS: dict[str, list[str]] = {
    "mp4": ["mp4_1080", "mp4_720", "mp4_480", "mp4_audio"],
    "mp3": ["mp3_320", "mp3_192", "mp3_128", "mp3_64"],
    "m4a": ["m4a_256", "m4a_128", "m4a_64"],
    "jpg": ["jpg_85", "jpg_75", "jpg_60", "jpg_40"],
    "png": ["png"],
    "audio": ["mp3_192", "m4a_128"],
}

# Combined format+quality presets for one-tap selection (first-time users).
_AUTO_PRESETS: dict[str, list[tuple[str, str, str]]] = {
    "video": [
        ("mp4", "mp4_1080", "MP4 1080p"),
        ("mp4", "mp4_720", "MP4 720p"),
        ("mp4", "mp4_480", "MP4 480p"),
        ("mp4", "mp4_audio", "MP4 → Audio (MP3)"),
    ],
    "audio": [
        ("mp3", "mp3_320", "MP3 320k"),
        ("mp3", "mp3_192", "MP3 192k"),
        ("mp3", "mp3_128", "MP3 128k"),
        ("mp3", "mp3_64", "MP3 64k"),
        ("m4a", "m4a_256", "M4A 256k"),
        ("m4a", "m4a_128", "M4A 128k"),
        ("m4a", "m4a_64", "M4A 64k"),
    ],
    "image": [
        ("jpg", "jpg_85", "JPEG 85%"),
        ("jpg", "jpg_75", "JPEG 75%"),
        ("jpg", "jpg_60", "JPEG 60%"),
        ("jpg", "jpg_40", "JPEG 40%"),
        ("png", "png", "PNG"),
    ],
}

_FORMAT_LABELS: dict[str, str] = {
    "mp4": "MP4 video",
    "mp3": "MP3 audio",
    "m4a": "M4A audio",
    "jpg": "JPEG image",
    "png": "PNG image",
    "audio": "Extract audio (MP3)",
}

# Per-user conversion state.
_convert_state: dict[int, _ConvertState] = {}


@dataclass
class _ConvertState:
    user_id: int
    kind: str  # "video", "audio", "image"
    input_path: Path
    tmpdir: Path
    lang: str
    status_message: Message
    container: str | None = None
    preset: str | None = None
    duration_s: float = 0.0
    cancelled: bool = False
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() - self.created_at > links.PENDING_TTL_S


def _detect_kind(message: Message) -> str | None:
    """Guess the media kind from a user-uploaded message."""
    video = getattr(message, "video", None) or getattr(message, "video_note", None)
    if video:
        return "video"
    audio = getattr(message, "audio", None) or getattr(message, "voice", None)
    if audio:
        return "audio"
    photo = getattr(message, "photo", None)
    if photo:
        return "image"
    document = getattr(message, "document", None)
    if document:
        mime = getattr(document, "mime_type", "") or ""
        name = (getattr(document, "file_name", "") or "").lower()
        if mime.startswith("video/") or name.endswith((".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv")):
            return "video"
        if mime.startswith("audio/") or name.endswith((".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac")):
            return "audio"
        if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")):
            return "image"
    return None


def _available_containers(kind: str) -> list[str]:
    if kind == "video":
        return ["mp4", "audio"]
    if kind == "audio":
        return ["mp3", "m4a"]
    if kind == "image":
        return ["jpg", "png"]
    return []


def _fmt_keyboard(kind: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=_FORMAT_LABELS[c],
                callback_data=f"{_FMT_PREFIX}:{kind}:{c}",
            )
        ]
        for c in _available_containers(kind)
    ]
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data=f"{_CANCEL_PREFIX}:{kind}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _qual_keyboard(kind: str, container: str) -> InlineKeyboardMarkup:
    presets = converter.video_presets() if kind == "video" else (
        converter.audio_presets() if kind == "audio" else converter.image_presets()
    )
    rows = [
        [
            InlineKeyboardButton(
                text=presets[p],
                callback_data=f"{_QUAL_PREFIX}:{kind}:{container}:{p}",
            )
        ]
        for p in _FORMAT_PRESETS.get(container, [])
    ]
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data=f"{_CANCEL_PREFIX}:{kind}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _auto_keyboard(kind: str) -> InlineKeyboardMarkup:
    """One-tap keyboard combining format + quality (first-time users)."""
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"{_AUTO_PREFIX}:{kind}:{container}:{preset}",
            )
        ]
        for container, preset, label in _AUTO_PRESETS.get(kind, [])
    ]
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data=f"{_CANCEL_PREFIX}:{kind}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _go_keyboard(kind: str, container: str, preset: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Convert",
                    callback_data=f"{_GO_PREFIX}:{kind}:{container}:{preset}",
                )
            ],
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"{_CANCEL_PREFIX}:{kind}")],
        ]
    )


def _control_keyboard(kind: str) -> InlineKeyboardMarkup:
    """Inline keyboard shown while a conversion is in progress."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"{_CANCEL_PREFIX}:{kind}")],
            [InlineKeyboardButton(text="⚙️ Change preset", callback_data=f"{_CHANGE_PREFIX}:{kind}")],
        ]
    )


@router.message(Command("convert"))
async def cmd_convert(message: Message, bot: Bot) -> None:
    """Entry point: explain the converter and ask for a media file."""
    srv = get_services()
    user = message.from_user
    if user is None:
        return
    lang = await register_user(srv.db, message)
    if await srv.db.is_banned(user.id):
        await message.answer(text(lang, "banned"))
        return
    await message.answer(text(lang, "convert_prompt"))


@router.message(
    F.document | F.video | F.audio | F.photo | F.voice | F.video_note
)
async def on_media_upload(message: Message, bot: Bot) -> None:
    """Receive a user-uploaded media file and convert it.

    If the user has a saved conversion preset, start converting immediately
    (auto-convert). Otherwise show a one-tap preset picker; the chosen preset
    is saved for future uploads.
    """
    srv = get_services()
    user = message.from_user
    if user is None:
        return
    lang = await register_user(srv.db, message)
    if await srv.db.is_banned(user.id):
        await message.answer(text(lang, "banned"))
        return
    if await srv.ratelimiter.has_active_job(user.id):
        await message.answer(text(lang, "active_job"))
        return

    kind = _detect_kind(message)
    if kind is None:
        await message.answer(text(lang, "convert_unsupported"))
        return

    # Determine file size for the early too-large check.
    file_obj = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)
    file_size = getattr(file_obj, "file_size", None) or 0
    if file_size and file_size > settings.converter_max_input_mb * 1024 * 1024:
        await message.answer(
            text(lang, "convert_file_too_large", limit=settings.converter_max_input_mb)
        )
        return

    tmpdir = links.make_tmpdir(user.id)
    try:
        document = getattr(message, "document", None)
        video = getattr(message, "video", None)
        audio = getattr(message, "audio", None)
        photo = getattr(message, "photo", None)
        voice = getattr(message, "voice", None)
        video_note = getattr(message, "video_note", None)

        file_id = (
            document.file_id
            if document
            else (video.file_id if video else audio.file_id if audio else None)
        )
        if file_id is None:
            if photo:
                file_id = photo[-1].file_id
            elif voice:
                file_id = voice.file_id
            elif video_note:
                file_id = video_note.file_id
        if file_id is None:
            await message.answer(text(lang, "convert_unsupported"))
            cleanup_dir(tmpdir)
            return

        status = await message.answer(text(lang, "convert_fetching"))
        input_path = tmpdir / "input"
        # bot.download() relies on TelegramAPIServer.wrap_local_file.to_local(),
        # which is a no-op identity function for local Bot API servers — it cannot
        # resolve server-side file paths to local ones. Download manually instead.
        file_info = await bot.get_file(file_id)
        file_url = bot.session.api.file_url(bot.token, file_info.file_path)
        stream = bot.session.stream_content(url=file_url, timeout=settings.download_timeout)
        try:
            async with aiofiles.open(input_path, "wb") as f:
                async for chunk in stream:
                    await f.write(chunk)
        finally:
            await stream.aclose()
    except Exception:  # noqa: BLE001
        cleanup_dir(tmpdir)
        await message.answer(text(lang, "convert_fetch_failed"))
        return

    try:
        info = await converter.probe_media(input_path)
    except Exception:  # noqa: BLE001
        log.info("converter probe failed", exc_info=True)
        cleanup_dir(tmpdir)
        await message.answer(text(lang, "convert_unsupported"))
        return

    # Re-check kind from probe (more reliable than mime guessing).
    kind = info.kind if info.kind != "unknown" else kind
    if kind == "unknown":
        cleanup_dir(tmpdir)
        await message.answer(text(lang, "convert_unsupported"))
        return

    state = _ConvertState(
        user_id=user.id,
        kind=kind,
        input_path=input_path,
        tmpdir=tmpdir,
        lang=lang,
        status_message=status,
        duration_s=info.duration_s or 0.0,
    )
    _convert_state[user.id] = state

    # Check for a saved preset; if present, auto-convert immediately.
    saved = await srv.db.get_convert_preset(user.id)
    if saved:
        try:
            saved_preset = json.loads(saved)
        except (TypeError, ValueError):
            saved_preset = None
        if saved_preset and isinstance(saved_preset, dict):
            container = saved_preset.get("container")
            preset = saved_preset.get("preset")
            if container and preset and container in _available_containers(kind):
                state.container = container
                state.preset = preset
                await _start_conversion_async(status, bot, state, lang)
                return

    # No saved preset: show one-tap preset picker.
    body = text(lang, "convert_auto_no_preset")
    await status.edit_text(body, reply_markup=_auto_keyboard(kind))


async def _start_conversion_async(status: Message, bot: Bot, state: _ConvertState, lang: str) -> None:
    """Async entry point for starting a conversion (rate-limit aware)."""
    srv = get_services()
    if not await srv.ratelimiter.start_job(state.user_id):
        await links.edit_message(message=status, body=text(lang, "active_job"))
        return

    semaphore = asyncio.Semaphore(settings.converter_max_concurrent)
    output_ext = {
        "mp4": ".mp4", "mp3": ".mp3", "m4a": ".m4a", "jpg": ".jpg", "png": ".png", "audio": ".mp3",
    }[state.container]
    output_path = state.tmpdir / f"converted{output_ext}"

    job = converter.ConvertJob(
        input_path=state.input_path,
        output_path=output_path,
        media_type=state.kind,
        preset=state.preset,
    )

    status_msg = status

    async def on_progress(pct: float) -> None:
        await links.edit_message(
            message=status_msg,
            body=text(lang, "convert_converting", pct=int(pct)),
            reply_markup=_control_keyboard(state.kind),
        )

    try:
        ok = await converter.convert_media(
            job,
            semaphore=semaphore,
            timeout=settings.converter_timeout_s,
            duration_s=state.duration_s,
        )
    except Exception:  # noqa: BLE001
        log.info("converter failed", exc_info=True)
        ok = False

    if state.cancelled or not ok:
        if state.cancelled:
            await links.edit_message(message=status_msg, body=text(lang, "convert_cancelled"))
        else:
            await links.edit_message(message=status_msg, body=text(lang, "convert_failed"))
        cleanup_dir(state.tmpdir)
        _convert_state.pop(state.user_id, None)
        await srv.ratelimiter.finish_job(state.user_id)
        return

    # Deliver: direct upload if under the limit, else signed download link.
    size = output_path.stat().st_size
    chat_id = status_msg.chat.id if status_msg else state.user_id

    if settings.fits_upload_limit(size):
        await _deliver_file(bot, chat_id, output_path, state.kind, lang)
    elif srv.download_links.enabled:
        try:
            result = await srv.download_links.create(
                srv.db, source=output_path, user_id=state.user_id, title=state.input_path.name
            )
            await links.edit_message(
                message=status_msg,
                body=text(
                    lang,
                    "link_ready",
                    limit=settings.max_mb,
                    hours=settings.download_link_ttl_hours,
                    url=result.url,
                ),
            )
        except Exception:  # noqa: BLE001
            log.info("converter link creation failed", exc_info=True)
            await links.edit_message(message=status_msg, body=text(lang, "convert_failed"))
    else:
        await links.edit_message(message=status_msg, body=text(lang, "convert_failed"))

    cleanup_dir(state.tmpdir)
    _convert_state.pop(state.user_id, None)
    await srv.ratelimiter.finish_job(state.user_id)


async def _deliver_file(bot: Bot, chat_id: int, path: Path, kind: str, lang: str) -> None:
    """Send a single converted file with the right Telegram method."""
    try:
        if kind == "video":
            await bot.send_video(chat_id, FSInputFile(path), supports_streaming=True)
        elif kind == "audio":
            await bot.send_audio(chat_id, FSInputFile(path))
        elif kind == "image":
            await bot.send_photo(chat_id, FSInputFile(path))
        else:
            await bot.send_document(chat_id, FSInputFile(path))
    except Exception:  # noqa: BLE001
        log.info("converter direct upload failed", exc_info=True)
        raise


@router.callback_query(F.data.startswith(_AUTO_PREFIX))
async def on_auto_convert(callback: CallbackQuery, bot: Bot) -> None:
    """First-time user picked a combined format+quality preset: save + convert."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        return
    kind = parts[2]
    container = parts[3]
    preset = parts[4]
    user = callback.from_user
    assert user is not None

    srv = get_services()
    lang = await srv.db.get_language(user.id) or settings.default_language

    state = _convert_state.get(user.id)
    if state is None or state.kind != kind:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return

    state.container = container
    state.preset = preset

    # Save preset for future auto-convert.
    preset_json = json.dumps({"kind": kind, "container": container, "preset": preset})
    await srv.db.set_convert_preset(user.id, preset_json)

    await _start_conversion_async(callback.message, bot, state, lang)


@router.callback_query(F.data.startswith(_FMT_PREFIX))
async def on_format_pick(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        return
    kind = parts[2]
    container = parts[3]
    user = callback.from_user
    assert user is not None
    srv = get_services()
    lang = await srv.db.get_language(user.id) or settings.default_language

    state = _convert_state.get(user.id)
    if state is None or state.kind != kind:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return
    if container not in _available_containers(kind):
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return

    state.container = container
    body = text(lang, "convert_choose_quality")
    await callback.message.edit_text(body, reply_markup=_qual_keyboard(kind, container))  # type: ignore[arg-type]


@router.callback_query(F.data.startswith(_QUAL_PREFIX))
async def on_quality_pick(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        return
    kind = parts[2]
    container = parts[3]
    preset = parts[4]
    user = callback.from_user
    assert user is not None
    srv = get_services()
    lang = await srv.db.get_language(user.id) or settings.default_language

    state = _convert_state.get(user.id)
    if state is None or state.kind != kind or state.container != container:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return

    presets = (
        converter.video_presets() if kind == "video" else converter.audio_presets() if kind == "audio" else converter.image_presets()
    )
    if preset not in presets:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return

    state.preset = preset

    # Save preset for future auto-convert.
    preset_json = json.dumps({"kind": kind, "container": container, "preset": preset})
    await srv.db.set_convert_preset(user.id, preset_json)

    await _start_conversion_async(callback.message, bot, state, lang)


@router.callback_query(F.data.startswith(_GO_PREFIX))
async def on_convert_go(callback: CallbackQuery, bot: Bot) -> None:
    """Start the conversion (manual path confirmation)."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        return
    kind = parts[2]
    container = parts[3]
    preset = parts[4]
    user = callback.from_user
    assert user is not None

    srv = get_services()
    lang = await srv.db.get_language(user.id) or settings.default_language

    state = _convert_state.get(user.id)
    if state is None or state.kind != kind or state.container != container or state.preset != preset:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return

    await _start_conversion_async(callback.message, bot, state, lang)


@router.callback_query(F.data.startswith(_CHANGE_PREFIX))
async def on_convert_change_preset(callback: CallbackQuery, bot: Bot) -> None:
    """User wants to pick a different preset during/after conversion."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        return
    kind = parts[2]
    user = callback.from_user
    assert user is not None
    srv = get_services()
    lang = await srv.db.get_language(user.id) or settings.default_language

    state = _convert_state.get(user.id)
    if state is None or state.kind != kind:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return

    # Cancel any in-progress conversion and show the picker.
    state.cancelled = True
    await callback.message.edit_text(
        text(lang, "convert_auto_no_preset"),
        reply_markup=_auto_keyboard(kind),
    )


@router.callback_query(F.data.startswith(_CANCEL_PREFIX))
async def on_convert_cancel(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        return
    kind = parts[2]
    user = callback.from_user
    assert user is not None
    state = _convert_state.get(user.id)
    if state is None or state.kind != kind:
        await callback.answer(text(settings.default_language, "expired_selection"), show_alert=True)
        return
    state.cancelled = True
    await callback.message.edit_text(text(state.lang, "convert_cancelling"))  # type: ignore[arg-type]
    cleanup_dir(state.tmpdir)
    _convert_state.pop(user.id, None)
