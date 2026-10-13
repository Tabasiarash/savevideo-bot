"""Inline mode.

Typing ``@bot <url>`` in any chat shows a cached result when we already have a
file_id for that URL, otherwise a deep-link article that opens the bot so the
normal download flow can run.
"""

from __future__ import annotations

import secrets

from aiogram import Bot, Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedVideo,
    InputTextMessageContent,
)

from bot.config import settings
from bot.deps import get_services
from bot.handlers.common import text
from bot.i18n import get_text, language_code_to_code
from bot.utils import URLBlockedError, check_url_safety, extract_urls, normalize_url

router = Router()


@router.inline_query()
async def on_inline_query(query: InlineQuery, bot: Bot) -> None:
    srv = get_services()
    user_id = query.from_user.id
    lang = await srv.db.get_language(user_id) or language_code_to_code(query.from_user.language_code, settings.default_language)

    results = await _build_results(srv, query.query, lang, bot)
    await query.answer(results=results, cache_time=60, is_personal=False)


async def _build_results(srv, raw: str, lang: str, bot: Bot) -> list[object]:
    urls = extract_urls(raw)
    if not urls:
        return [InlineQueryResultArticle(
            id="hint",
            title=text(lang, "url_invalid"),
            description=text(lang, "inline_no_results"),
            input_message_content=InputTextMessageContent(message_text=text(lang, "inline_no_results")),
        )]
    url = urls[0]
    try:
        url = normalize_url(url)
        await check_url_safety(url)
    except (URLBlockedError, ValueError):
        return [InlineQueryResultArticle(
            id="blocked",
            title=text(lang, "url_blocked"),
            description=text(lang, "url_blocked"),
            input_message_content=InputTextMessageContent(message_text=text(lang, "url_blocked")),
        )]

    hash_url = _url_hash(url)
    cached_video = await srv.cache.get(hash_url, "video_best")
    if cached_video:
        return [InlineQueryResultCachedVideo(
            id=f"cv{cached_video.file_id[:20]}",
            video_file_id=cached_video.file_id,
            title=cached_video.title or text(lang, "inline_deeplink_title", title="video"),
            description=text(lang, "cache_immediate"),
            caption=text(lang, "cache_immediate"),
            input_message_content=InputTextMessageContent(message_text=text(lang, "cache_immediate")),
        )]

    cached_audio = await srv.cache.get(hash_url, "audio_mp3")
    if cached_audio:
        return [InlineQueryResultCachedAudio(
            id=f"ca{cached_audio.file_id[:20]}",
            audio_file_id=cached_audio.file_id,
            caption=text(lang, "cache_immediate"),
        )]

    token = secrets.token_urlsafe(8)
    await srv.db.deeplink_create(token, url)
    botname = (await bot.me()).username or "bot"
    deep_link = f"https://t.me/{botname}?start=dl_{token}"
    return [InlineQueryResultArticle(
        id=f"dl_{token}",
        title=text(lang, "inline_article_title"),
        description=get_text(lang, "inline_article_desc", botname=botname),
        url=deep_link,
        input_message_content=InputTextMessageContent(
            message_text=text(lang, "inline_article_title") + " — " + url
        ),
    )]


def _url_hash(url: str) -> str:
    from bot.db import url_hash

    return url_hash(url)
