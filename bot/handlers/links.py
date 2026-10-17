"""Core link flow: URL detection → info → format picker → download → upload.

Also implements deep-link starts, playlist mode and media-groups for multi-item
posts. Any file over the Bot API limit (or that fails to upload) is kept on disk
and delivered as a signed, expiring download link instead of being split or
re-encoded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
)

from bot.config import settings
from bot.db import url_hash
from bot.deps import get_services
from bot.handlers.common import is_banned, register_user, text
from bot.i18n import get_text, language_code_to_code
from bot.services import subtitles
from bot.services.download_links import LinkError
from bot.services.downloader import (
    DownloadError,
    DownloadJob,
    InfoError,
    YtDlpError,
    cleanup_dir,
    disk_free_bytes,
    probe_duration,
    probe_media,
    video_quality_rows,
)
from bot.services.subtitles import SubtitleOption
from bot.utils import (
    URLBlockedError,
    URLParseError,
    check_url_safety,
    extract_urls,
    format_duration,
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

# A post-download hook may rewrite/move the produced files in place (e.g. tag an
# audio file) before it is uploaded or linked. It is only invoked when set.
PostprocessHook = Callable[[list[Path]], Awaitable[None]]


@dataclass
class AudioMeta:
    """Optional audio tags applied on upload (used by metadata-matched flows)."""

    title: str | None = None
    performer: str | None = None
    cover: Path | None = None


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
    lang: str
    status_message: Message | None
    # Overrides for handlers that prepare a flow independently (metadata match):
    # the cache key can differ from the download URL and audio can be tagged.
    cache_key: str | None = None
    audio_meta: AudioMeta | None = None
    postprocess: PostprocessHook | None = None
    # Subtitle flow: the options offered with the picker, the quality the user
    # picked (needed because the subtitle/mode prompts act across callbacks) and
    # the final subtitle/mode selection.
    subtitle_options: list[SubtitleOption] = field(default_factory=list)
    chosen_format: str | None = None
    chosen_subtitle: SubtitleOption | None = None
    chosen_mode: str | None = None
    # For very long videos in burn mode: the start (seconds) of the 15-minute
    # window to burn, and whether a window was chosen at all.
    chosen_start: float = 0.0
    chosen_segment: bool = False
    # When the flow was prepared; a picker older than PENDING_TTL_S is stale and
    # its buttons answer with "expired_selection" instead of acting on it.
    created_at: float = field(default_factory=time.time)

    @property
    def url_hash(self) -> str:
        return self.cache_key or url_hash(self.url)


# A picker left idle this long is dropped so a stale inline keyboard cannot act.
PENDING_TTL_S = 15 * 60

_pending: dict[int, PendingFlow] = {}


def _flow_expired(flow: PendingFlow) -> bool:
    return (time.time() - flow.created_at) > PENDING_TTL_S


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

    want_subs = settings.enable_subtitles and not playlist
    try:
        info = await _extract(srv, url, playlist=playlist, tmpdir=tmpdir, want_subtitles=want_subs)
    except InfoError as exc:
        await _on_info_error(status_message, lang, user.id, url, exc)
        cleanup_dir(tmpdir)
        return

    if not info:
        cleanup_dir(tmpdir)
        await _edit(message=status_message, body=text(lang, "no_formats"))
        return

    duration = float(info.get("duration") or 0)
    if duration and duration > settings.max_duration_s and not settings.is_admin(user.id):
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
        lang=lang,
        status_message=status_message,
    )
    if want_subs:
        flow.subtitle_options = subtitles.available_subtitle_options(info, lang)
        # Don't retain the (potentially hundreds of) caption maps in the flow or
        # logs: the two/three picks above are all we need.
        info.pop("automatic_captions", None)
        info.pop("subtitles", None)
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

    if _flow_expired(flow):
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        _cleanup_pending(flow)
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

    if fmt.startswith("sub:"):
        await callback.answer()
        await _on_subtitle_pick(callback, bot, flow, fmt[len("sub:") :], lang)
        return

    if fmt.startswith("submode:"):
        await callback.answer()
        await _on_submode_pick(callback, bot, flow, fmt[len("submode:") :], lang)
        return

    if fmt.startswith("seg:"):
        await callback.answer()
        await _on_segment_pick(callback, bot, flow, fmt[len("seg:") :], lang)
        return

    await callback.answer()
    if _should_offer_subtitles(flow, fmt):
        flow.chosen_format = fmt
        flow.chosen_subtitle = None
        flow.chosen_mode = None
        await _edit(
            message=callback.message,
            body=_subtitle_text(flow),
            reply_markup=_subtitle_keyboard(flow, 0),
        )
        return
    await _run_download(callback, bot, flow, fmt)


def _should_offer_subtitles(flow: PendingFlow, fmt: str) -> bool:
    return (
        settings.enable_subtitles
        and bool(flow.subtitle_options)
        and fmt.startswith("video")
        and flow.prefix != "pl"
        and not _is_gallery(flow)
    )


async def _on_subtitle_pick(callback: CallbackQuery, bot: Bot, flow: PendingFlow, value: str, lang: str) -> None:
    if value == "none":
        flow.chosen_subtitle = None
        flow.chosen_mode = None
        await _run_download(callback, bot, flow, flow.chosen_format or "")
        return
    if value.startswith("more:"):
        try:
            page = int(value[len("more:") :])
        except ValueError:
            return
        await _edit(message=callback.message, body=_subtitle_text(flow), reply_markup=_subtitle_keyboard(flow, page))
        return
    try:
        index = int(value)
        option = flow.subtitle_options[index]
    except (ValueError, IndexError):
        return
    flow.chosen_subtitle = option
    flow.chosen_mode = settings.normalize_subtitle_mode(await _stored_subtitle_mode(callback.from_user.id))
    await _edit(
        message=callback.message,
        body=_subtitle_mode_text(flow),
        reply_markup=_subtitle_mode_keyboard(flow),
    )


async def _on_submode_pick(callback: CallbackQuery, bot: Bot, flow: PendingFlow, value: str, lang: str) -> None:
    if not flow.chosen_subtitle:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return
    flow.chosen_mode = settings.normalize_subtitle_mode(value)
    if flow.chosen_mode == subtitles.BURN and _segment_cap_s() and flow.duration > _segment_cap_s():
        # Long video in burn mode: let the user pick which 15-minute window to burn.
        flow.chosen_segment = False
        flow.chosen_start = 0.0
        await _edit(
            message=callback.message,
            body=_segment_text(flow, lang),
            reply_markup=_segment_keyboard(flow, lang),
        )
        return
    await _run_download(callback, bot, flow, flow.chosen_format or "")


def _segment_cap_s() -> float:
    if not settings.enable_subtitles:
        return 0.0
    return float(settings.subtitle_burn_max_s)


def _segment_end(flow: PendingFlow) -> float:
    return max(0.0, flow.duration - _segment_cap_s())


def _clamp_start(flow: PendingFlow, start: float) -> float:
    return min(max(0.0, start), _segment_end(flow))


async def _on_segment_pick(callback: CallbackQuery, bot: Bot, flow: PendingFlow, value: str, lang: str) -> None:
    if value == "cancel":
        await callback.answer(text(lang, "cancel"))
        if _pending.get(callback.from_user.id) is flow:
            _pending.pop(callback.from_user.id, None)
        cleanup_dir(flow.tmpdir)
        await _try_delete(callback.message)
        return
    if value == "go":
        flow.chosen_segment = True
        flow.chosen_start = _clamp_start(flow, flow.chosen_start)
        await _run_download(callback, bot, flow, flow.chosen_format or "")
        return
    delta = {"15": 900.0, "5": 300.0, "-15": -900.0, "-5": -300.0}.get(value)
    if delta is None:
        await callback.answer(text(lang, "expired_selection"), show_alert=True)
        return
    flow.chosen_start = _clamp_start(flow, flow.chosen_start + delta)
    await _edit(
        message=callback.message,
        body=_segment_text(flow, lang),
        reply_markup=_segment_keyboard(flow, lang),
    )


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

def _min_free_ok() -> bool:
    try:
        return disk_free_bytes(settings.temp_dir_resolved) >= settings.min_free_disk_bytes
    except OSError:
        return True


def disk_space_ok() -> bool:
    """Public check for callers that download to disk before delivering (e.g. podcast enclosures)."""
    return _min_free_ok()


async def download_flow(bot: Bot, flow: PendingFlow, fmt: str, user_id: int) -> str:
    """Queue a prepared flow, download and deliver it.

    Shared entry point for every handler. Returns ``"ok"`` when the attempt ran,
    ``"disk_low"`` (already shown to the user) or ``"active_job"`` (the caller
    surfaces it, e.g. as a callback alert) when it was rejected before starting.
    """
    srv = get_services()

    if not _min_free_ok():
        await _edit(message=flow.status_message, body=text(flow.lang, "disk_low"))
        _cleanup_pending(flow)
        cleanup_dir(flow.tmpdir)
        return "disk_low"

    if not await srv.ratelimiter.start_job(user_id):
        return "active_job"

    admission = await srv.queue.enter()
    try:
        await _do_download(srv, bot, flow, fmt, user_id, admission.position)
    finally:
        await srv.queue.leave()
        await srv.ratelimiter.finish_job(user_id)
    return "ok"


async def _run_download(callback: CallbackQuery, bot: Bot, flow: PendingFlow, fmt: str) -> None:
    result = await download_flow(bot, flow, fmt, callback.from_user.id)
    if result == "active_job":
        await callback.answer(text(flow.lang, "active_job"), show_alert=True)


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
    option = flow.chosen_subtitle
    if option is not None and flow.prefix != "pl":
        job.subtitle_langs = option.key
        job.subtitle_auto = option.kind in ("auto", "translated")
    cap = _segment_cap_s()
    if (
        option is not None
        and fmt.startswith("video")
        and flow.chosen_mode == subtitles.BURN
        and flow.chosen_segment
        and cap
        and flow.duration > cap
    ):
        start = _clamp_start(flow, flow.chosen_start)
        job.section = (start, min(flow.duration, start + cap))
    editor = _ProgressEditor(flow)

    try:
        paths = await _run_download_job(srv, job, editor, write_thumbnail=fmt.startswith("video"))
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:  # noqa: BLE001 — yt-dlp raises many error types
        await _on_download_error(flow, user_id, exc)
        _cleanup_pending(flow)
        cleanup_dir(flow.tmpdir)
        return

    parts = _nonempty(paths)
    try:
        if not parts:
            await _on_download_error(flow, user_id, DownloadError("no media produced"))
            _cleanup_pending(flow)
            cleanup_dir(flow.tmpdir)
            return
        if flow.postprocess is not None:
            await flow.postprocess(parts)
        srt_path = None
        if option is not None and flow.chosen_mode:
            srt_path = await _apply_subtitles(srv, bot, flow, parts, fmt)
        total = sum(p.stat().st_size for p in parts)
        if srt_path is not None and srt_path.exists():
            total += srt_path.stat().st_size
        consumed = await _deliver(srv, bot, flow, parts, fmt, user_id, lang, total, srt_path=srt_path)
    finally:
        if consumed:
            _cleanup_pending(flow)
            cleanup_dir(flow.tmpdir)
        # else keep picker alive (over-limit / upload-fallback to link)


async def _deliver(
    srv,
    bot: Bot,
    flow: PendingFlow,
    parts: list[Path],
    fmt: str,
    user_id: int,
    lang: str,
    total: int,
    *,
    srt_path: Path | None = None,
) -> bool:
    """Send as one Telegram message, or hand over a link when too large / failed.

    Returns True if the flow was consumed (uploaded to Telegram or terminal
    error), False if the picker should remain alive (link delivered).
    """
    if not settings.fits_upload_limit(total):
        await _send_links(srv, flow, parts, fmt, user_id, lang, total, keep_picker=True)
        await _send_srt(bot, flow, srt_path, user_id)
        return False

    try:
        await _upload_with_retry(srv, bot, flow, parts, fmt, user_id, lang)
        await _send_srt(bot, flow, srt_path, user_id)
        return True
    except Exception as exc:  # noqa: BLE001
        if _link_would_help(exc) and srv.download_links.enabled:
            log.info("upload failed (%s); delivering a link instead", type(exc).__name__)
            await _send_links(srv, flow, parts, fmt, user_id, lang, total, keep_picker=True)
            await _send_srt(bot, flow, srt_path, user_id)
            return False
        log.warning("upload failed user_id=%s site=%s: %s", user_id, flow.url_site, exc)
        await _record(srv.db, user_id, flow.url, flow.url_site, fmt, "error", "telegram", total, flow.duration)
        await _edit(message=flow.status_message, body=text(lang, "error_upload", error=str(exc)[:300]))
        return True


async def deliver_local_file(bot: Bot, flow: PendingFlow, path: Path, user_id: int) -> str:
    """Deliver an already-downloaded local file (podcast enclosure path).

    The file was fetched by the caller's own hardened HTTP client; yt-dlp is
    deliberately not involved. Any tags are applied via ``flow.postprocess`` and
    the shared upload/link logic is reused. Returns ``"ok"``/``"disk_low"``/
    ``"active_job"`` like :func:`download_flow`.
    """
    srv = get_services()
    lang = flow.lang

    if not _min_free_ok():
        await _edit(message=flow.status_message, body=text(lang, "disk_low"))
        _cleanup_pending(flow)
        cleanup_dir(flow.tmpdir)
        return "disk_low"

    if not await srv.ratelimiter.start_job(user_id):
        return "active_job"

    admission = await srv.queue.enter()
    try:
        if admission.position > 1:
            await _edit(message=flow.status_message, body=text(lang, "downloading_position", pos=admission.position))
        else:
            await _edit(message=flow.status_message, body=text(lang, "downloading"))
        parts = _nonempty([path])
        try:
            if not parts:
                await _on_download_error(flow, user_id, DownloadError("no media produced"))
                return "ok"
            if flow.postprocess is not None:
                await flow.postprocess(parts)
            total = sum(p.stat().st_size for p in parts)
            if not settings.fits_upload_limit(total):
                await _send_links(srv, flow, parts, "audio_mp3", user_id, lang, total, keep_picker=False)
            else:
                try:
                    await _upload_with_retry(srv, bot, flow, parts, "audio_mp3", user_id, lang)
                except Exception as exc:  # noqa: BLE001
                    if _link_would_help(exc) and srv.download_links.enabled:
                        log.info("upload failed (%s); delivering a link instead", type(exc).__name__)
                        await _send_links(srv, flow, parts, "audio_mp3", user_id, lang, total, keep_picker=False)
                    else:
                        log.warning("upload failed user_id=%s site=%s: %s", user_id, flow.url_site, exc)
                        await _record(
                            srv.db, user_id, flow.url, flow.url_site, "audio_mp3", "error", "telegram", total, flow.duration
                        )
                        await _edit(message=flow.status_message, body=text(lang, "error_upload", error=str(exc)[:300]))
        finally:
            _cleanup_pending(flow)
            cleanup_dir(flow.tmpdir)
    finally:
        await srv.queue.leave()
        await srv.ratelimiter.finish_job(user_id)
    return "ok"


async def _upload_with_retry(
    srv, bot: Bot, flow: PendingFlow, parts: list[Path], fmt: str, user_id: int, lang: str
) -> None:
    """Upload once; retry a single time on a transient error before giving up."""
    try:
        await _upload_once(srv, bot, flow, parts, fmt, user_id, lang)
    except TelegramForbiddenError:
        raise
    except Exception:  # noqa: BLE001
        await _upload_once(srv, bot, flow, parts, fmt, user_id, lang)


async def _upload_once(
    srv, bot: Bot, flow: PendingFlow, parts: list[Path], fmt: str, user_id: int, lang: str
) -> None:
    if fmt.startswith("audio"):
        await _upload_audio_flow(srv, bot, flow, parts, fmt, user_id, lang)
    else:
        await _upload_video_flow(srv, bot, flow, parts, fmt, user_id, lang)


def _link_would_help(exc: Exception) -> bool:
    """A download link only helps when the failure is not the user's own chat."""
    if isinstance(exc, TelegramForbiddenError):
        return False
    lowered = str(exc).lower()
    return not any(k in lowered for k in ("blocked", "chat not found", "deactivated", "kicked"))


async def _send_links(
    srv, flow: PendingFlow, parts: list[Path], fmt: str, user_id: int, lang: str, total: int, *, keep_picker: bool = False
) -> None:
    store = srv.download_links
    if not store.enabled:
        await _edit(message=flow.status_message, body=text(lang, "link_unavailable", limit=settings.max_mb))
        await _record(srv.db, user_id, flow.url, flow.url_site, fmt, "error", "link_disabled", total, flow.duration)
        return

    results = []
    for path in parts:
        try:
            results.append(await store.create(srv.db, source=path, user_id=user_id, title=flow.title))
        except LinkError as exc:
            await _edit(message=flow.status_message, body=_link_error_text(lang, exc))
            await _record(
                srv.db, user_id, flow.url, flow.url_site, fmt, "error", f"link_{exc.reason}", total, flow.duration
            )
            return

    body = "\n\n".join(
        text(lang, "link_ready", limit=settings.max_mb, hours=settings.download_link_ttl_hours, url=r.url)
        for r in results
    )
    if keep_picker:
        # Keep the picker alive so user can pick a lower-quality option that
        # fits Telegram's upload limit and get the actual file instead of a link.
        header = _choose_text(flow)
        combined = f"{body}\n\n{header}" if header else body
        await _edit(message=flow.status_message, body=combined, reply_markup=_build_keyboard(flow))
    else:
        await _edit(message=flow.status_message, body=body)
    for result in results:
        await _record(srv.db, user_id, flow.url, flow.url_site, fmt, "link", None, result.size_bytes, flow.duration)
    await srv.db.touch_user(user_id)


def _link_error_text(lang: str, exc: LinkError) -> str:
    if exc.reason == "quota_user":
        return text(lang, "link_quota_user", max=settings.download_link_max_active)
    if exc.reason == "quota_total":
        return text(lang, "link_quota_total")
    return text(lang, "link_failed")


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


async def _upload_video_flow(srv, bot: Bot, flow: PendingFlow, parts: list[Path], fmt: str, user_id: int, lang: str) -> None:
    chat_id = flow.status_message.chat.id if flow.status_message else user_id
    for path in parts:
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
            await srv.cache.put(flow.url_hash, _cache_format(flow, fmt), sent.video.file_id, flow.title, size)
        await _record(srv.db, user_id, flow.url, flow.url_site, fmt, "ok", None, size, flow.duration)
        await srv.db.touch_user(user_id)
    await _try_delete(flow.status_message)


async def _upload_audio_flow(srv, bot: Bot, flow: PendingFlow, parts: list[Path], fmt: str, user_id: int, lang: str) -> None:
    chat_id = flow.status_message.chat.id if flow.status_message else user_id
    meta = flow.audio_meta
    for path in parts:
        media = await probe_media(path)
        kwargs: dict[str, Any] = {
            "title": (meta.title if meta and meta.title else sanitize_filename(flow.title)) or None,
            "performer": (meta.performer if meta and meta.performer else sanitize_filename(flow.uploader)) or None,
            "duration": int(media.duration or flow.duration) or None,
        }
        if meta and meta.cover and meta.cover.exists():
            kwargs["thumbnail"] = FSInputFile(meta.cover)
        sent = await bot.send_audio(chat_id, FSInputFile(path), **kwargs)
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


async def _extract(srv, url: str, playlist: bool, tmpdir: Path, *, want_subtitles: bool = False) -> dict[str, Any] | None:
    items = "1-10" if playlist else None
    try:
        return await srv.runner.extract_info(
            url,
            playlist=playlist,
            playlist_items=items,
            cookiejar_dir=tmpdir,
            want_subtitles=want_subtitles,
        )
    except InfoError as exc:
        if srv.updater.should_retry(str(exc)):
            log.info("info extraction failed (%s); updating yt-dlp and retrying", exc)
            result = await srv.updater.update()
            if result.ok:
                return await srv.runner.extract_info(
                    url,
                    playlist=playlist,
                    playlist_items=items,
                    cookiejar_dir=tmpdir,
                    want_subtitles=want_subtitles,
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


def _height_of(fmt: str) -> int | None:
    if fmt.startswith("video_"):
        try:
            return int(fmt.split("_", 1)[1])
        except ValueError:
            return None
    return None


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


def make_tmpdir(user_id: int) -> Path:
    """Create and return a fresh per-job temp directory (shared by handlers)."""
    tmpdir = _new_tmpdir(user_id)
    tmpdir.mkdir(parents=True, exist_ok=True)
    return tmpdir


def remember_flow(user_id: int, flow: PendingFlow) -> None:
    """Register a prepared flow so the shared picker callbacks can act on it."""
    _pending[user_id] = flow


def picker_keyboard(flow: PendingFlow) -> InlineKeyboardMarkup:
    """Build the format picker keyboard for a prepared flow."""
    return _build_keyboard(flow)


async def edit_message(
    *,
    message: Message | None,
    body: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    disable_preview: bool = False,
) -> None:
    """Public wrapper around the internal safe-edit helper."""
    await _edit(message=message, body=body, reply_markup=reply_markup, disable_preview=disable_preview)


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
    marker = text(flow.lang, "link_marker")
    for quality_row in video_quality_rows(
        flow.info,
        prefix=flow.prefix,
        limit_bytes=settings.upload_limit_bytes,
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text=option.label + (marker if option.over_limit else ""),
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


# --------------------------------------------------------------------------
# Subtitle UI and post-processing
# --------------------------------------------------------------------------

def _option_label(option: SubtitleOption, lang: str) -> str:
    if option.kind == "auto":
        return option.name + text(lang, "subtitle_auto")
    if option.kind == "translated":
        return option.name + text(lang, "subtitle_auto_translated")
    return option.name


def _subtitle_text(flow: PendingFlow) -> str:
    return text(flow.lang, "choose_subtitle", title=sanitize_filename(flow.title) or "?")


def _subtitle_keyboard(flow: PendingFlow, page: int) -> InlineKeyboardMarkup:
    size = max(1, settings.subtitle_page_size)
    manual = [o for o in flow.subtitle_options if o.kind == "manual"]
    auto = [o for o in flow.subtitle_options if o.kind != "manual"]
    pages = max(1, (len(manual) + size - 1) // size)
    page = min(max(0, page), pages - 1)
    rows: list[list[InlineKeyboardButton]] = []
    start = page * size
    for offset, option in enumerate(manual[start : start + size]):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_option_label(option, flow.lang),
                    callback_data=f"{flow.prefix}:sub:{start + offset}",
                )
            ]
        )
    base = len(manual)
    for offset, option in enumerate(auto):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_option_label(option, flow.lang),
                    callback_data=f"{flow.prefix}:sub:{base + offset}",
                )
            ]
        )
    if pages > 1 and page < pages - 1:
        rows.append(
            [InlineKeyboardButton(text=text(flow.lang, "button_more"), callback_data=f"{flow.prefix}:sub:more:{page + 1}")]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=text(flow.lang, "button_no_subtitles"), callback_data=f"{flow.prefix}:sub:none"
            ),
            InlineKeyboardButton(text=text(flow.lang, "button_cancel"), callback_data=f"{flow.prefix}:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _subtitle_mode_text(flow: PendingFlow) -> str:
    option = flow.chosen_subtitle
    language = _option_label(option, flow.lang) if option else "?"
    body = text(flow.lang, "choose_subtitle_mode", language=language)
    return f"{body}\n\n{text(flow.lang, 'subtitle_mode_hint')}"


def _subtitle_mode_keyboard(flow: PendingFlow) -> InlineKeyboardMarkup:
    selected = settings.normalize_subtitle_mode(flow.chosen_mode)

    def _label(key: str, mode: str) -> str:
        mark = "✅ " if mode == selected else ""
        return mark + text(flow.lang, key)

    rows = [
        [
            InlineKeyboardButton(
                text=_label("button_mode_burn", subtitles.BURN), callback_data=f"{flow.prefix}:submode:burn"
            )
        ],
        [
            InlineKeyboardButton(
                text=_label("button_mode_embed", subtitles.EMBED), callback_data=f"{flow.prefix}:submode:embed"
            ),
            InlineKeyboardButton(
                text=_label("button_mode_srt", subtitles.SRT), callback_data=f"{flow.prefix}:submode:srt"
            ),
        ],
        [InlineKeyboardButton(text=text(flow.lang, "button_cancel"), callback_data=f"{flow.prefix}:cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_clock(seconds: float) -> str:
    total = int(max(0, seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def _segment_text(flow: PendingFlow, lang: str) -> str:
    cap = _segment_cap_s()
    start = _clamp_start(flow, flow.chosen_start)
    return text(
        lang,
        "subtitle_window_prompt",
        duration=_fmt_clock(flow.duration),
        max=int(cap // 60) or 1,
        start=_fmt_clock(start),
        end=_fmt_clock(start + cap),
    )


def _segment_keyboard(flow: PendingFlow, lang: str) -> InlineKeyboardMarkup:
    def nudge(key: str, value: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text(lang, key), callback_data=f"{flow.prefix}:seg:{value}")

    rows = [
        [
            nudge("button_seg_minus15", "-15"),
            nudge("button_seg_minus5", "-5"),
            nudge("button_seg_plus5", "5"),
            nudge("button_seg_plus15", "15"),
        ],
        [
            InlineKeyboardButton(text=text(lang, "button_seg_burn"), callback_data=f"{flow.prefix}:seg:go"),
            InlineKeyboardButton(text=text(lang, "button_cancel"), callback_data=f"{flow.prefix}:seg:cancel"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _stored_subtitle_mode(user_id: int) -> str | None:
    try:
        return await get_services().db.get_subtitle_mode(user_id)
    except Exception:  # noqa: BLE001 - a missing preference just falls back to default
        log.debug("failed to read subtitle mode", exc_info=True)
        return None


def _cache_format(flow: PendingFlow, fmt: str) -> str:
    if flow.chosen_subtitle and flow.chosen_mode:
        key = f"{fmt}#sub:{flow.chosen_subtitle.key}:{flow.chosen_mode}"
        if flow.chosen_mode == subtitles.BURN and flow.chosen_segment:
            key += f"@{int(flow.chosen_start)}"
        return key
    return fmt


def _pick_subtitle_file(tmpdir: Path) -> Path | None:
    files = subtitles.collect_subtitle_files(tmpdir)
    return files[0] if files else None


def _write_clean_srt(src: Path, tmpdir: Path) -> Path | None:
    try:
        raw = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cleaned = subtitles.clean_subtitle_text(raw)
    if not cleaned.strip():
        return None
    out = tmpdir / f"{src.stem}.clean.srt"
    try:
        out.write_text(cleaned, encoding="utf-8")
    except OSError:
        return None
    return out


def _burn_bitrate(target: Path, duration: float) -> int | None:
    """Target video bitrate when a burn-in would exceed Telegram's limit."""
    try:
        size = target.stat().st_size
    except OSError:
        return None
    limit = int(settings.upload_limit_bytes * 0.9)
    if size <= limit:
        return None
    return subtitles.target_bitrate_kbps(duration, limit)


async def _apply_subtitles(srv, bot: Bot, flow: PendingFlow, parts: list[Path], fmt: str) -> Path | None:
    """Burn/embed the downloaded subtitle track, or return an ``.srt`` to send."""
    option = flow.chosen_subtitle
    mode = flow.chosen_mode
    if option is None or not mode or not parts:
        return None
    srt = _pick_subtitle_file(flow.tmpdir)
    if srt is None:
        await _edit(message=flow.status_message, body=text(flow.lang, "subtitle_failed"))
        return None
    cleaned = _write_clean_srt(srt, flow.tmpdir)
    if cleaned is None:
        await _edit(message=flow.status_message, body=text(flow.lang, "subtitle_failed"))
        return None

    # Audio cannot carry a subtitle track; the file itself is the delivery.
    if mode == subtitles.SRT or not fmt.startswith("video"):
        return cleaned

    target = parts[0]
    if mode == subtitles.BURN:
        cap = _segment_cap_s()
        if flow.chosen_segment and cap and (not flow.duration or flow.duration > cap):
            return await _burn_window(flow, target, cleaned, cap)
        if flow.duration and flow.duration > cap:
            await _edit(
                message=flow.status_message,
                body=text(flow.lang, "subtitle_burn_too_long", max=settings.subtitle_burn_max_minutes),
            )
        else:
            editor = _ProgressEditor(flow)

            async def _progress(percent: float) -> None:
                await editor.update(percent, 0, 0)

            ok = await subtitles.burn_subtitles(
                target,
                cleaned,
                settings,
                duration_s=flow.duration,
                bitrate_kbps=_burn_bitrate(target, flow.duration),
                on_progress=_progress,
            )
            if ok:
                return None
            await _edit(message=flow.status_message, body=text(flow.lang, "subtitle_failed"))
        return cleaned

    if await subtitles.embed_subtitles(target, cleaned, language=option.code):
        return None
    return cleaned


async def _burn_window(flow: PendingFlow, target: Path, cleaned: Path, cap: float) -> Path | None:
    """Burn the selected window of a long video (full-burn not possible)."""
    start = _clamp_start(flow, flow.chosen_start)
    clip_dur = await probe_duration(target)
    if clip_dur > cap + 5:
        # The download ignored the section request: trim here (seek resets PTS).
        seek = start
        offset: float | None = start
    else:
        # The file already is the window; rebase cues onto its real start PTS.
        first_pts = await subtitles.probe_start_time(target)
        seek = 0.0
        offset = start - first_pts
    editor = _ProgressEditor(flow)

    async def _progress(percent: float) -> None:
        await editor.update(percent, 0, 0)

    ok = await subtitles.burn_segment(
        target,
        cleaned,
        settings,
        start_s=start,
        duration_s=cap,
        seek_s=seek,
        offset_s=offset,
        bitrate_kbps=_burn_bitrate(target, cap),
        on_progress=_progress,
    )
    if ok:
        return None
    await _edit(message=flow.status_message, body=text(flow.lang, "subtitle_failed"))
    return cleaned


async def _send_srt(bot: Bot, flow: PendingFlow, srt_path: Path | None, user_id: int) -> None:
    if srt_path is None or not srt_path.exists():
        return
    chat_id = flow.status_message.chat.id if flow.status_message else user_id
    name = f"{sanitize_filename(flow.title) or 'subtitles'}.srt"
    try:
        await bot.send_document(chat_id, FSInputFile(srt_path, filename=name), caption=flow.title or None)
    except Exception as exc:  # noqa: BLE001
        log.warning("subtitle document upload failed user_id=%s: %s", user_id, exc)


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


async def _edit(
    *,
    message: Message | None,
    body: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    disable_preview: bool = False,
) -> None:
    if message is None:
        return
    try:
        link_preview = LinkPreviewOptions(is_disabled=True) if disable_preview else None
        if link_preview is not None:
            if reply_markup is not None:
                await message.edit_text(body, reply_markup=reply_markup, link_preview_options=link_preview)
            else:
                await message.edit_text(body, link_preview_options=link_preview)
        elif reply_markup is not None:
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
