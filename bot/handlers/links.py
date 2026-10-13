"""Core link flow: URL detection → info → format picker → download → upload.

Also implements deep-link starts, playlist mode, media-groups for multi-item
posts and the size-limit fallback chain (lower quality → ffmpeg re-encode →
clear error).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from bot.config import settings
from bot.db import url_hash
from bot.deps import get_services
from bot.handlers.common import is_banned, register_user, text
from bot.i18n import get_text, language_code_to_code
from bot.services.downloader import (
    DownloadError,
    DownloadJob,
    InfoError,
    YtDlpError,
    best_estimate,
    cleanup_dir,
    estimate_format_size,
    probe_media,
    transcode_audio_to_size,
    transcode_to_size,
    video_quality_rows,
)
from bot.utils import (
    URLBlockedError,
    URLParseError,
    check_url_safety,
    extract_urls,
    format_duration,
    format_size,
    normalize_url,
    sanitize_filename,
    site_from_url,
    validate_redirect_chain,
)

log = logging.getLogger(__name__)

router = Router()

_PREFIXES = ("dl", "pl")
_GALLERY_SITES = {"instagram", "facebook", "twitter", "x", "tumblr", "reddit", "pinterest"}
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


@dataclass
class PendingFlow:
    url: str
    url_site: str
    info: dict[str, Any]
    tmpdir: Path
    title: str
    duration: float
    uploader: str
    prefix: str
    playlist_items: str | None
    video_heights: list[int]
    lang: str
    status_message: Message | None

    @property
    def url_hash(self) -> str:
        return url_hash(self.url)


_pending: dict[int, PendingFlow] = {}


# --------------------------------------------------------------------------
# Entry: plain text message containing URLs
# --------------------------------------------------------------------------

@router.message(F.text)
async def on_text_message(message: Message) -> None:
    if not message.text:
        return
    if message.text.startswith("/"):
        await message.answer(text(await _lang_for(message), "unknown_command"))
        return
    urls = extract_urls(message.text)
    if not urls:
        await message.answer(text(await _lang_for(message), "unknown_command"))
        return
    await handle_urls(message, urls, playlist=False)


async def _lang_for(message: Message) -> str:
    srv = get_services()
    user = message.from_user
    if user is None:
        return settings.default_language
    stored = await srv.db.get_language(user.id)
    if stored:
        return stored
    return language_code_to_code(user.language_code, settings.default_language)


async def handle_urls(message: Message, urls: list[str], playlist: bool) -> None:
    """Validate a URL, extract info and present the format picker."""
    srv = get_services()
    user = message.from_user
    if user is None:
        return
    lang = await register_user(srv.db, message)

    if await is_banned(srv.db, user.id):
        await message.answer(text(lang, "banned"))
        return
    if await srv.ratelimiter.has_active_job(user.id):
        await message.answer(text(lang, "active_job"))
        return

    if len(urls) > 1:
        await message.answer(text(lang, "url_multiple", count=len(urls)))
    raw_url = urls[0]

    if not _supported(raw_url):
        await message.answer(text(lang, "url_only_http"))
        return
    try:
        url = normalize_url(raw_url)
    except URLParseError:
        await message.answer(text(lang, "url_only_http"))
        return

    try:
        await check_url_safety(url)
        await validate_redirect_chain(url)
    except URLBlockedError:
        await message.answer(text(lang, "url_blocked"))
        log.warning("ssrf_blocked user_id=%s site=%s", user.id, site_from_url(url))
        return

    status = await srv.ratelimiter.check(user.id)
    if not status.allowed:
        if status.reason is not None and status.reason.value == "daily":
            await message.answer(text(lang, "daily_limit", limit=settings.per_user_daily_limit))
        else:
            await message.answer(text(lang, "rate_limited", seconds=status.seconds))
        return
    await srv.ratelimiter.begin_request(user.id)

    tmpdir = _new_tmpdir(user.id)
    tmpdir.mkdir(parents=True, exist_ok=True)
    status_message = await message.answer(text(lang, "url_fetching"))

    try:
        info = await _extract(srv, url, playlist=playlist, tmpdir=tmpdir)
    except InfoError as exc:
        await _on_info_error(status_message, lang, user.id, url, exc)
        cleanup_dir(tmpdir)
        return

    if not info:
        cleanup_dir(tmpdir)
        await _edit(message=status_message, body=text(lang, "no_formats"))
        return

    duration = float(info.get("duration") or 0)
    if duration and duration > settings.max_duration_s:
        minutes = int(duration // 60)
        await _edit(
            message=status_message,
            body=text(lang, "duration_too_long", minutes=minutes, max=settings.max_duration_min),
        )
        await _record(srv.db, user.id, url, site_from_url(url), "", "error", "duration_limit", None, duration)
        cleanup_dir(tmpdir)
        return

    flow = PendingFlow(
        url=url,
        url_site=site_from_url(url),
        info=info,
        tmpdir=tmpdir,
        title=str(info.get("title") or ""),
        duration=duration,
        uploader=str(info.get("uploader") or info.get("channel") or ""),
        prefix="pl" if playlist else "dl",
        playlist_items="1-10" if playlist else None,
        video_heights=_heights_from(info),
        lang=lang,
        status_message=status_message,
    )
    _pending[user.id] = flow

    if playlist and info.get("entries"):
        count = len(info["entries"])
        if count > settings.playlist_max_items:
            await _edit(message=status_message, body=text(lang, "playlist_too_long", count=settings.playlist_max_items))

    await _edit(
        message=status_message,
        body=_choose_text(flow),
        reply_markup=_build_keyboard(flow),
    )


async def handle_deeplink(message: Message, bot: Bot, token: str) -> None:
    """Entry for ``t.me/<bot>?start=dl_<token>`` produced by inline mode."""
    srv = get_services()
    user = message.from_user
    if user is None:
        return
    lang = await register_user(srv.db, message)
    row = await srv.db.deeplink_get(token)
    if not row or (int(time.time()) - int(row["created_at"])) > 24 * 3600:
        await message.answer(text(lang, "deeplink_expired"))
        await srv.db.deeplink_delete(token)
        return
    url = str(row["url"])
    await srv.db.deeplink_delete(token)
    await message.answer(text(lang, "deeplink_start"))
    await handle_urls(message, [url], playlist=False)


# --------------------------------------------------------------------------
# Format picker callbacks
# --------------------------------------------------------------------------

@router.callback_query(F.data.startswith(tuple(_PREFIXES)))
async def on_format_pick(callback: CallbackQuery, bot: Bot) -> None:
    user = callback.from_user
    flow = _pending.get(user.id)
    prefix, _, fmt = callback.data.partition(":")
    lang = await _lang_from_db(user.id, user.language_code)

    if flow is None or flow.prefix != prefix:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return

    if fmt == "cancel":
        await callback.answer(text(lang, "cancel"))
        await _try_delete(callback.message)
        if _pending.get(user.id) is flow:
            _pending.pop(user.id, None)
        cleanup_dir(flow.tmpdir)
        return

    if fmt == "thumbnail":
        await callback.answer()
        await _send_thumbnail(callback, bot, flow, lang)
        return

    await callback.answer()
    await _run_download(callback, bot, flow, fmt)


async def _send_thumbnail(callback: CallbackQuery, bot: Bot, flow: PendingFlow, lang: str) -> None:
    srv = get_services()
    job = DownloadJob(url=flow.url, format_key="thumbnail", tmpdir=flow.tmpdir, title_hint=sanitize_filename(flow.title))
    try:
        thumb = await srv.runner.download_thumbnail(job)
    except YtDlpError as exc:
        await _edit(message=flow.status_message, body=text(lang, "error_download", error=str(exc)))
        await _record(srv.db, callback.from_user.id, flow.url, flow.url_site, "thumbnail", "error", _classify(exc), None, flow.duration)
        return
    if not thumb:
        await _edit(message=flow.status_message, body=text(lang, "no_formats"))
        return
    try:
        await bot.send_photo(callback.from_user.id, FSInputFile(thumb))
    except Exception as exc:  # noqa: BLE001
        log.warning("thumbnail upload failed user_id=%s: %s", callback.from_user.id, exc)
    await _try_delete(flow.status_message)


# --------------------------------------------------------------------------
# Download orchestration
# --------------------------------------------------------------------------

def _estimate_selected(flow: PendingFlow, fmt: str) -> int | None:
    """Estimated download size for the chosen format, when it can be known."""
    if fmt == "video_best":
        return best_estimate(flow.info, settings.max_video_height)
    if fmt.startswith("video_"):
        height = _height_of(fmt)
        return estimate_format_size(flow.info, height) if height else None
    return None


async def _run_download(callback: CallbackQuery, bot: Bot, flow: PendingFlow, fmt: str) -> None:
    srv = get_services()
    user_id = callback.from_user.id
    lang = flow.lang

    estimate = _estimate_selected(flow, fmt)
    if estimate is not None and not settings.fits_upload_limit(estimate):
        await _edit(
            message=flow.status_message,
            body=text(
                lang,
                "too_large_estimate",
                size=format_size(estimate),
                limit=settings.max_mb,
            ),
        )
        await _record(
            srv.db, user_id, flow.url, flow.url_site, fmt, "error", "too_large", estimate, flow.duration
        )
        _cleanup_pending(flow)
        cleanup_dir(flow.tmpdir)
        return

    if not await srv.ratelimiter.start_job(user_id):
        await callback.answer(text(lang, "active_job"), show_alert=True)
        return

    admission = await srv.queue.enter()
    try:
        await _do_download(srv, bot, flow, fmt, user_id, admission.position)
    finally:
        await srv.queue.leave()
        await srv.ratelimiter.finish_job(user_id)


async def _do_download(srv, bot: Bot, flow: PendingFlow, fmt: str, user_id: int, position: int) -> None:
    lang = flow.lang

    if position > 1:
        await _edit(message=flow.status_message, body=text(lang, "downloading_position", pos=position))
    else:
        await _edit(message=flow.status_message, body=text(lang, "downloading"))

    if _is_gallery(flow) and fmt == "video_best":
        await _download_gallery(srv, bot, flow, user_id, lang)
        return

    job = DownloadJob(
        url=flow.url,
        format_key=fmt,
        tmpdir=flow.tmpdir,
        title_hint=sanitize_filename(flow.title) or "media",
        height=_height_of(fmt),
        audio_codec="m4a" if fmt == "audio_m4a" else None,
        playlist=flow.prefix == "pl",
        playlist_items=flow.playlist_items,
    )
    editor = _ProgressEditor(flow)

    try:
        paths = await _run_download_job(srv, job, editor, write_thumbnail=fmt.startswith("video"))
        sizes = [p.stat().st_size for p in _nonempty(paths)]
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 — yt-dlp raises many error types
        await _on_download_error(flow, user_id, exc)
        _cleanup_pending(flow)
        return

    total = sum(sizes)
    limit = settings.upload_limit_bytes
    parts = _nonempty(paths)

    if fmt.startswith("video"):
        parts = await _fit_video_size(srv, flow, fmt, parts, total, limit)
        if parts is None:
            _cleanup_pending(flow)
            return
    elif fmt.startswith("audio") and total > limit:
        parts = await _fit_audio_size(srv, flow, parts, total, limit)
        if parts is None:
            _cleanup_pending(flow)
            return

    try:
        if fmt.startswith("audio"):
            await _upload_audio_flow(srv, bot, flow, parts, fmt, user_id, lang)
        else:
            await _upload_video_flow(srv, bot, flow, parts, fmt, user_id, lang)
    except Exception as exc:  # noqa: BLE001
        log.warning("upload failed user_id=%s site=%s: %s", user_id, flow.url_site, exc)
        await _record(srv.db, user_id, flow.url, flow.url_site, fmt, "error", "telegram", total, flow.duration)
        await _edit(message=flow.status_message, body=text(lang, "error_upload", error=str(exc)[:300]))
    finally:
        _cleanup_pending(flow)
        cleanup_dir(flow.tmpdir)


async def _run_download_job(srv, job: DownloadJob, editor: _ProgressEditor, write_thumbnail: bool) -> list[Path]:
    """Download with one automatic yt-dlp update + retry on extractor errors."""
    attempts = 0
    while True:
        attempts += 1
        try:
            return await srv.runner.download(
                job,
                on_progress=editor.update,
                write_thumbnail=write_thumbnail,
            )
        except YtDlpError as exc:
            if attempts == 1 and srv.updater.should_retry(str(exc)):
                log.info("extractor error (%s); updating yt-dlp and retrying once", exc)
                await srv.updater.update()
                continue
            raise


async def _fit_video_size(
    srv, flow: PendingFlow, fmt: str, parts: list[Path], total: int, limit: int
) -> list[Path] | None:
    """Fallback chain: next lower height → ffmpeg re-encode → clear error."""
    lang = flow.lang
    current = parts[0]
    current_size = total
    current_fmt = fmt
    current_height = _height_of(fmt)

    while current_size > limit:
        if current_fmt.startswith("video") and current_height:
            next_h = _next_lower_height(flow, current_height)
            if next_h is not None:
                await _edit(message=flow.status_message, body=text(lang, "file_too_large_fallback"))
                job = DownloadJob(
                    url=flow.url,
                    format_key=f"video_{next_h}",
                    tmpdir=flow.tmpdir,
                    title_hint=sanitize_filename(flow.title) or "media",
                    height=next_h,
                )
                editor = _ProgressEditor(flow)
                try:
                    paths = await _run_download_job(srv, job, editor, write_thumbnail=True)
                except YtDlpError:
                    break
                parts = _nonempty(paths)
                if not parts:
                    break
                current = parts[0]
                current_size = sum(p.stat().st_size for p in parts)
                current_height = next_h
                continue

        await _edit(message=flow.status_message, body=text(lang, "reencoded", size=format_size(limit)))
        try:
            outdir = flow.tmpdir / "fitsize"
            outdir.mkdir(parents=True, exist_ok=True)
            encoded = await transcode_to_size(current, outdir, limit, flow.duration or 60.0)
            return [encoded]
        except Exception as exc:  # noqa: BLE001
            log.warning("ffmpeg re-encode failed: %s", exc)
            break

    if current_size > limit:
        await _edit(message=flow.status_message, body=text(lang, "too_large_impossible", limit=settings.max_mb))
        await _record(srv.db, 0, flow.url, flow.url_site, fmt, "error", "too_large", current_size, flow.duration)
        return None
    return parts


async def _fit_audio_size(srv, flow: PendingFlow, parts: list[Path], total: int, limit: int) -> list[Path] | None:
    lang = flow.lang
    current = parts[0]
    await _edit(message=flow.status_message, body=text(lang, "reencoded", size=format_size(limit)))
    try:
        outdir = flow.tmpdir / "fitsize"
        outdir.mkdir(parents=True, exist_ok=True)
        encoded = await transcode_audio_to_size(current, outdir, limit, flow.duration or 60.0)
        if encoded.stat().st_size <= limit:
            return [encoded]
    except Exception as exc:  # noqa: BLE001
        log.warning("audio re-encode failed: %s", exc)

    await _edit(message=flow.status_message, body=text(lang, "too_large_impossible", limit=settings.max_mb))
    return None


async def _upload_video_flow(srv, bot: Bot, flow: PendingFlow, parts: list[Path], fmt: str, user_id: int, lang: str) -> None:
    chat_id = flow.status_message.chat.id if flow.status_message else user_id
    for index, path in enumerate(parts, start=1):
        if len(parts) > 1:
            await _edit(message=flow.status_message, body=text(lang, "sending_part", part=index, total=len(parts)))
        media = await probe_media(path)
        thumb = _find_thumbnail(flow.tmpdir)
        kwargs: dict[str, Any] = {
            "supports_streaming": True,
            "width": media.width or _width_of(flow),
            "height": media.height or _height_of(fmt),
            "duration": int(media.duration or flow.duration) or None,
        }
        if thumb:
            kwargs["thumbnail"] = FSInputFile(thumb)
        try:
            sent = await bot.send_video(chat_id, FSInputFile(path), **kwargs)
        except Exception as exc:  # noqa: BLE001
            if "thumbnail" in str(exc).lower():
                kwargs.pop("thumbnail", None)
                sent = await bot.send_video(chat_id, FSInputFile(path), **kwargs)
            else:
                raise
        size = path.stat().st_size
        if len(parts) == 1:
            await srv.cache.put(flow.url_hash, fmt, sent.video.file_id, flow.title, size)
        await _record(srv.db, user_id, flow.url, flow.url_site, fmt, "ok", None, size, flow.duration)
        await srv.db.touch_user(user_id)
    await _try_delete(flow.status_message)


async def _upload_audio_flow(srv, bot: Bot, flow: PendingFlow, parts: list[Path], fmt: str, user_id: int, lang: str) -> None:
    chat_id = flow.status_message.chat.id if flow.status_message else user_id
    for index, path in enumerate(parts, start=1):
        if len(parts) > 1:
            await _edit(message=flow.status_message, body=text(lang, "sending_part", part=index, total=len(parts)))
        media = await probe_media(path)
        sent = await bot.send_audio(
            chat_id,
            FSInputFile(path),
            title=sanitize_filename(flow.title) or None,
            performer=sanitize_filename(flow.uploader) or None,
            duration=int(media.duration or flow.duration) or None,
        )
        size = path.stat().st_size
        if len(parts) == 1:
            await srv.cache.put(flow.url_hash, fmt, sent.audio.file_id, flow.title, size)
        await _record(srv.db, user_id, flow.url, flow.url_site, fmt, "ok", None, size, flow.duration)
        await srv.db.touch_user(user_id)
    await _try_delete(flow.status_message)


async def _download_gallery(srv, bot: Bot, flow: PendingFlow, user_id: int, lang: str) -> None:
    entries = [e for e in flow.info.get("entries") or [] if e][: settings.max_media_group]
    if not entries:
        return

    chat_id = flow.status_message.chat.id if flow.status_message else user_id
    await _edit(message=flow.status_message, body=text(lang, "media_group", count=len(entries)))

    media: list[Any] = []
    total_size = 0
    for n, entry in enumerate(entries[: settings.max_media_group], start=1):
        url = entry.get("url") or entry.get("webpage_url")
        if not url:
            continue
        job = DownloadJob(
            url=url,
            format_key="video_best",
            tmpdir=flow.tmpdir,
            title_hint=sanitize_filename(entry.get("title") or f"{flow.title}_{n}") or "media",
        )
        editor = _ProgressEditor(flow)
        try:
            paths = await _run_download_job(srv, job, editor, write_thumbnail=True)
        except YtDlpError as exc:
            await _edit(message=flow.status_message, body=text(lang, "error_download", error=str(exc)))
            return
        if not paths:
            continue
        path = paths[0]
        total_size += path.stat().st_size
        if path.suffix.lower() in _IMAGE_SUFFIXES:
            media.append(InputMediaPhoto(type="photo", media=FSInputFile(path)))
        else:
            media.append(InputMediaVideo(type="video", media=FSInputFile(path), supports_streaming=True))

    if media:
        try:
            await bot.send_media_group(chat_id, media)
        except Exception as exc:  # noqa: BLE001 — mixed groups are invalid; send individually
            log.warning("media group failed (%s), sending individually", exc)
            for item in media:
                if item.type == "photo":
                    await bot.send_photo(chat_id, item.media)
                else:
                    await bot.send_video(chat_id, item.media, supports_streaming=True)
        await _try_delete(flow.status_message)
        await _record(srv.db, user_id, flow.url, flow.url_site, "video_best", "ok", None, total_size, flow.duration)
        await srv.db.touch_user(user_id)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _cleanup_pending(flow: PendingFlow) -> None:
    for user_id, candidate in list(_pending.items()):
        if candidate is flow:
            _pending.pop(user_id, None)
            return


async def _lang_from_db(user_id: int, language_code: str | None) -> str:
    srv = get_services()
    stored = await srv.db.get_language(user_id)
    if stored:
        return stored
    return language_code_to_code(language_code, settings.default_language)


def _supported(raw: str) -> bool:
    from bot.utils import is_supported_scheme

    return is_supported_scheme(raw)


async def _extract(srv, url: str, playlist: bool, tmpdir: Path) -> dict[str, Any] | None:
    items = "1-10" if playlist else None
    try:
        return await srv.runner.extract_info(
            url, playlist=playlist, playlist_items=items, cookiejar_dir=tmpdir
        )
    except InfoError as exc:
        if srv.updater.should_retry(str(exc)):
            log.info("info extraction failed (%s); updating yt-dlp and retrying", exc)
            result = await srv.updater.update()
            if result.ok:
                return await srv.runner.extract_info(
                    url, playlist=playlist, playlist_items=items, cookiejar_dir=tmpdir
                )
        raise


async def _on_info_error(status_message: Message | None, lang: str, user_id: int, url: str, exc: InfoError) -> None:
    srv = get_services()
    body = text(lang, "youtube_signin") if _is_signin_error(str(exc)) else text(lang, "error_info", error=str(exc)[:300])
    await _edit(message=status_message, body=body)
    await _record(srv.db, user_id, url, site_from_url(url), "", "error", _classify(exc), None, None)


async def _on_download_error(flow: PendingFlow, user_id: int, exc: Exception) -> None:
    srv = get_services()
    body = (
        text(flow.lang, "youtube_signin")
        if _is_signin_error(str(exc))
        else text(flow.lang, "error_download", error=str(exc)[:300])
    )
    await _edit(message=flow.status_message, body=body)
    await _record(srv.db, user_id, flow.url, flow.url_site, "", "error", _classify(exc), None, flow.duration)


def _classify(exc: Exception) -> str:
    if isinstance(exc, (InfoError, DownloadError, YtDlpError)):
        return f"ytdlp:{type(exc).__name__}"
    return f"other:{type(exc).__name__}"


def _is_signin_error(error: str) -> bool:
    lowered = error.lower()
    return "confirm you're not a bot" in lowered or "sign in to confirm" in lowered


async def _record(db, user_id: int, url: str, site: str, format_key: str, status: str, error: str | None, size: int | None, duration: float | None) -> None:
    try:
        await db.record_download(user_id, url_hash(url), site, format_key, status, error, size, duration)
    except Exception:  # noqa: BLE001
        log.debug("failed to record download row", exc_info=True)


def _is_gallery(flow: PendingFlow) -> bool:
    entries = flow.info.get("entries") or []
    return flow.url_site in _GALLERY_SITES and len(entries) > 1


def _heights_from(info: dict[str, Any]) -> list[int]:
    return sorted(
        {
            int(f["height"])
            for f in info.get("formats") or []
            if f.get("height") and f.get("vcodec") and f.get("vcodec") != "none"
        },
        reverse=True,
    )


def _height_of(fmt: str) -> int | None:
    if fmt.startswith("video_"):
        try:
            return int(fmt.split("_", 1)[1])
        except ValueError:
            return None
    return None


def _next_lower_height(flow: PendingFlow, height: int | None) -> int | None:
    heights = flow.video_heights
    if not heights:
        return None
    if height is None:
        return heights[0]
    if height not in heights:
        return heights[0]
    idx = heights.index(height)
    if idx >= len(heights) - 1:
        return None
    return heights[idx + 1]


def _width_of(flow: PendingFlow) -> int | None:
    return int(flow.info["width"]) if flow.info.get("width") else None


def _nonempty(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.exists() and p.stat().st_size > 0]


def _find_thumbnail(tmpdir: Path) -> Path | None:
    for candidate in sorted(tmpdir.iterdir()):
        if candidate.suffix.lower() in (".jpg", ".jpeg", ".png") and "fitsize" not in str(candidate):
            return candidate
    return None


def _new_tmpdir(user_id: int) -> Path:
    stamp = f"u{user_id}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    return settings.temp_dir_resolved / stamp


def _choose_text(flow: PendingFlow) -> str:
    if not flow.title:
        return text(flow.lang, "choose_format_no_meta")
    return text(
        flow.lang,
        "choose_format",
        title=sanitize_filename(flow.title),
        duration=format_duration(flow.duration),
        uploader=sanitize_filename(flow.uploader) or "?",
    )


def _build_keyboard(flow: PendingFlow) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    marker = text(flow.lang, "too_large_marker")
    for quality_row in video_quality_rows(
        flow.info,
        prefix=flow.prefix,
        limit_bytes=settings.upload_limit_bytes,
        max_height=settings.max_video_height,
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text=option.label + (marker if option.too_large else ""),
                    callback_data=option.key,
                )
                for option in quality_row
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=text(flow.lang, "button_audio_mp3"), callback_data=f"{flow.prefix}:audio_mp3"),
            InlineKeyboardButton(text=text(flow.lang, "button_audio_m4a"), callback_data=f"{flow.prefix}:audio_m4a"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=text(flow.lang, "button_thumbnail"), callback_data=f"{flow.prefix}:thumbnail"),
            InlineKeyboardButton(text=text(flow.lang, "button_cancel"), callback_data=f"{flow.prefix}:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


class _ProgressEditor:
    def __init__(self, flow: PendingFlow) -> None:
        self.flow = flow
        self.last_edit = 0.0

    async def update(self, percent: float, downloaded: int, total: int) -> None:
        now = asyncio.get_running_loop().time()
        if now - self.last_edit < settings.progress_edit_interval:
            return
        self.last_edit = now
        if self.flow.status_message is None:
            return
        await _edit(message=self.flow.status_message, body=get_text(self.flow.lang, "progress", pct=round(percent, 1)))


async def _edit(*, message: Message | None, body: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    if message is None:
        return
    try:
        if reply_markup is not None:
            await message.edit_text(body, reply_markup=reply_markup)
        else:
            await message.edit_text(body)
    except Exception:  # noqa: BLE001 — user may have deleted the message
        pass


async def _try_delete(message: Message | None) -> None:
    if message is None:
        return
    with contextlib.suppress(Exception):
        await message.delete()
