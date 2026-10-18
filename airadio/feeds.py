"""RSS-hírforrások letöltése és szűrése: csak dátummal ellátott, friss hír marad."""

from __future__ import annotations

import calendar
import html
import logging
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser

from .config import Feed

log = logging.getLogger(__name__)

USER_AGENT = "airadio/0.1 (+szemelyes radio)"
SUMMARY_LIMIT = 700


@dataclass(frozen=True)
class NewsItem:
    source: str
    title: str
    summary: str
    link: str
    published: datetime


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


def parse_feed(feed: Feed, payload: bytes, now: datetime, max_age: timedelta) -> list[NewsItem]:
    parsed = feedparser.parse(payload)
    items = []
    for entry in parsed.entries:
        published = _published(entry)
        title = _clean(entry.get("title", ""))
        if published is None or not title:
            continue  # dátum és cím nélkül nem ellenőrizhető, kimarad
        if published < now - max_age or published > now + timedelta(hours=1):
            continue
        summary = _clean(entry.get("summary", ""))
        if len(summary) > SUMMARY_LIMIT:
            summary = summary[:SUMMARY_LIMIT].rsplit(" ", 1)[0] + "…"
        items.append(
            NewsItem(
                source=feed.name,
                title=title,
                summary=summary,
                link=entry.get("link", ""),
                published=published,
            )
        )
    return items


def download(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch(feeds: tuple[Feed, ...], max_age_hours: float, now: datetime | None = None) -> list[NewsItem]:
    """Az összes forrás friss hírei, a legújabb elöl, cím és link szerint egyedien."""
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=max_age_hours)
    items: list[NewsItem] = []
    for feed in feeds:
        try:
            items.extend(parse_feed(feed, download(feed.url), now, max_age))
        except Exception as e:  # egy hibás forrás ne vigye el a többit
            log.warning("%s (%s) nem tölthető le: %s", feed.name, feed.url, e)

    seen: set[str] = set()
    unique = []
    for item in sorted(items, key=lambda i: i.published, reverse=True):
        keys = {item.title.casefold(), item.link} - {""}
        if keys & seen:
            continue
        seen |= keys
        unique.append(item)
    return unique
