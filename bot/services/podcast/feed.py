"""Parse a podcast RSS/Atom feed for an episode's enclosure.

Feeds are untrusted input fetched from arbitrary hosts, so this is defensive:

* **defusedxml** only (the stdlib parser is open to entity expansion /
  billion-laughs).
* The feed body is capped at ``PODCAST_MAX_FEED_BYTES``.
* At most ``PODCAST_MAX_FEED_ITEMS`` items are read.
* Each item's enclosure must be an ``http(s)`` ``audio/*`` or octet-stream URL.

The enclosure is the publisher's own file — exactly what a podcast app
downloads — and is fetched separately by :mod:`.download`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from defusedxml import ElementTree as SafeET

from bot.services.spotify.http import fetch

log = logging.getLogger(__name__)

_AUDIO_TYPES = ("audio/", "application/ogg", "application/octet-stream")
_DURATION_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+)$")


@dataclass
class FeedItem:
    title: str | None = None
    audio_url: str | None = None
    audio_type: str | None = None
    duration_s: float | None = None
    released: str | None = None
    guid: str | None = None


def _local(tag: str) -> str:
    """``{namespace}name`` -> ``name`` (iTunes/Atom namespaces are common)."""
    return tag.rsplit("}", 1)[-1].lower()


def _text(elem, names: tuple[str, ...]) -> str | None:
    for child in elem.iter():
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return None


def _attr(elem, names: tuple[str, ...]) -> str | None:
    for key, value in elem.attrib.items():
        if _local(key) in names and value:
            return value.strip()
    return None


def _parse_duration(value: str | None) -> float | None:
    """``HH:MM:SS`` / ``MM:SS`` / bare seconds -> seconds."""
    if not value:
        return None
    value = value.strip()
    match = _DURATION_RE.match(value)
    if match:
        hours = int(match.group(1) or 0)
        return float(hours * 3600 + int(match.group(2)) * 60 + int(match.group(3)))
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def _is_audio(type_attr: str | None) -> bool:
    if not type_attr:
        return True  # many feeds omit the type; the URL decides downstream
    lowered = type_attr.lower()
    return any(lowered.startswith(prefix) or lowered == prefix for prefix in _AUDIO_TYPES)


def _usable_audio_url(url: str | None) -> bool:
    if not url:
        return False
    split = urlsplit(url)
    return split.scheme in ("http", "https") and bool(split.netloc)


def _item_to_feed_item(item_elem) -> FeedItem | None:
    item = FeedItem(title=_text(item_elem, ("title",)))

    for elem in item_elem.iter():
        tag = _local(elem.tag)
        if tag == "enclosure":
            url = _attr(elem, ("url",))
            type_attr = _attr(elem, ("type",))
            if _usable_audio_url(url) and _is_audio(type_attr):
                item.audio_url = url
                item.audio_type = type_attr
                break
        elif tag in ("content", "link"):
            # Atom: <link rel="enclosure" href="..."/> or media:content.
            rel = (_attr(elem, ("rel",)) or "").lower()
            href = _attr(elem, ("href", "url"))
            type_attr = _attr(elem, ("type",))
            if (rel == "enclosure" or tag == "content") and _usable_audio_url(href) and _is_audio(type_attr):
                item.audio_url = href
                item.audio_type = type_attr
                break

    if not item.audio_url:
        return None

    item.duration_s = _parse_duration(_text(item_elem, ("duration",)))
    item.released = _text(item_elem, ("pubdate", "published", "updated"))
    item.guid = _text(item_elem, ("guid", "id"))
    return item


def parse_feed(xml_bytes: bytes, *, max_items: int) -> list[FeedItem]:
    """Parse a feed into enclosure-bearing items, capped at ``max_items``."""
    try:
        root = SafeET.fromstring(xml_bytes)
    except Exception:  # noqa: BLE001 - malformed/untrusted XML must not raise
        log.info("podcast feed parse failed", exc_info=True)
        return []

    items: list[FeedItem] = []
    for elem in root.iter():
        tag = _local(elem.tag)
        if tag not in ("item", "entry"):  # RSS 2.0 <item> / Atom <entry>
            continue
        parsed = _item_to_feed_item(elem)
        if parsed is not None:
            items.append(parsed)
            if len(items) >= max_items:
                break
    return items


async def list_enclosures(
    feed_url: str,
    *,
    proxy: str | None = None,
    max_bytes: int,
    max_items: int,
) -> list[FeedItem]:
    """Fetch and parse a feed, honouring the size and item caps."""
    if not _usable_audio_url(feed_url):
        return []
    try:
        resp = await fetch(feed_url, proxy=proxy, timeout=20.0, max_bytes=max_bytes)
    except Exception:  # noqa: BLE001
        log.info("podcast feed fetch failed", exc_info=True)
        return []
    if resp.status != 200 or not resp.content:
        return []
    return parse_feed(resp.content, max_items=max_items)
