"""Fetch items from blog and personal RSS/Atom feeds."""

import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
_FETCH_TIMEOUT = 15  # seconds; feedparser uses urllib under the hood


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _parse_published(entry: feedparser.FeedParserDict) -> datetime | None:
    """Return a timezone-aware datetime from whichever date field is present."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            # feedparser gives time.struct_time in UTC
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _strip_html(text: str | None) -> str:
    """Very light HTML tag removal — good enough for a raw summary field."""
    if not text:
        return ""
    import re
    return re.sub(r"<[^>]+>", "", text).strip()


def _normalize(entry: feedparser.FeedParserDict, source_name: str) -> dict:
    summary_raw = (
        getattr(entry, "summary", None)
        or getattr(entry, "description", None)
        or ""
    )
    return {
        "title": getattr(entry, "title", "").strip(),
        "url": getattr(entry, "link", "").strip(),
        "source": source_name,
        "published": _parse_published(entry).isoformat() if _parse_published(entry) else None,
        "summary_raw": _strip_html(summary_raw),
    }


def _fetch_feed(name: str, url: str, cutoff: datetime) -> list[dict]:
    """Fetch one feed and return items newer than cutoff. Returns [] on any error."""
    try:
        # feedparser doesn't natively support a timeout; set via socket default
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_FETCH_TIMEOUT)
        try:
            feed = feedparser.parse(url)
        finally:
            socket.setdefaulttimeout(old_timeout)

        if feed.bozo and not feed.entries:
            # bozo means malformed feed; if there are still entries we keep going
            logger.warning("Malformed feed '%s' (%s): %s", name, url, feed.bozo_exception)
            return []

        items = []
        for entry in feed.entries:
            pub = _parse_published(entry)
            if pub is None:
                # No date — include it; we can't filter it out confidently
                items.append(_normalize(entry, name))
            elif pub >= cutoff:
                items.append(_normalize(entry, name))

        logger.info("'%s': %d item(s) in last 24 h (feed had %d total)", name, len(items), len(feed.entries))
        return items

    except Exception as exc:
        logger.error("Failed to fetch feed '%s' (%s): %s", name, url, exc)
        return []


def fetch(sources: list[dict]) -> list[dict]:
    """Return normalized items from the given list of {name, url} dicts, last 24 h only."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    results = []
    for source in sources:
        name = source.get("name", source.get("url", "unknown"))
        url = source.get("url", "")
        if not url:
            logger.warning("Skipping source '%s': no URL", name)
            continue
        results.extend(_fetch_feed(name, url, cutoff))
    return results


def fetch_all() -> list[dict]:
    """Convenience: load config and fetch both blogs and people RSS feeds."""
    config = _load_config()

    blog_sources = config.get("blogs", [])

    people_sources = [
        {"name": p["name"], "url": p["blog_rss"]}
        for p in config.get("people", [])
        if p.get("blog_rss")
    ]

    return fetch(blog_sources + people_sources)
