"""Fetch recent papers from the arXiv Atom API."""

import logging
import re
import socket
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

import feedparser
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
_BASE_URL = "https://export.arxiv.org/api/query"
_FETCH_TIMEOUT = 30  # arXiv can be slow; give it more headroom than a blog feed


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _build_url(categories: list[str], max_results: int) -> str:
    # Combine categories into a single OR query so we make one request and
    # get back a deduplicated, date-sorted result set from arXiv directly.
    search_query = " OR ".join(f"cat:{c}" for c in categories)
    params = urlencode({
        "search_query": search_query,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    return f"{_BASE_URL}?{params}"


def _parse_published(entry: feedparser.FeedParserDict) -> datetime | None:
    # Use published (original submission) rather than updated (revision) so
    # revised old papers don't surface as "new".
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _strip_whitespace(text: str) -> str:
    """Collapse newlines and extra spaces — arXiv abstracts often have both."""
    return re.sub(r"\s+", " ", text).strip()


def _normalize(entry: feedparser.FeedParserDict) -> dict:
    # Prefer the https abstract link; feedparser exposes it as entry.link
    url = getattr(entry, "link", "").strip()
    # arXiv also puts the PDF link in entry.links; keep the abstract URL only
    title = _strip_whitespace(getattr(entry, "title", ""))
    summary = _strip_whitespace(getattr(entry, "summary", ""))
    pub = _parse_published(entry)

    return {
        "title": title,
        "url": url,
        "source": "arXiv",
        "published": pub.isoformat() if pub else None,
        "summary_raw": summary,
    }


def fetch(config: dict, *, cutoff: datetime | None = None) -> list[dict]:
    """Return normalized paper dicts for categories in config, last 24 h only.

    config keys used: category (str), extra_categories (list[str]), max_results (int)
    """
    if cutoff is None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    primary = config.get("category", "cs.AI")
    extras = config.get("extra_categories", [])
    categories = [primary] + [c for c in extras if c != primary]
    max_results = int(config.get("max_results", 50))

    url = _build_url(categories, max_results)
    logger.info("Fetching arXiv: %s", url)

    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_FETCH_TIMEOUT)
        try:
            feed = feedparser.parse(url)
        finally:
            socket.setdefaulttimeout(old_timeout)

        if feed.bozo and not feed.entries:
            logger.warning("Malformed arXiv response: %s", feed.bozo_exception)
            return []

        items = []
        seen_urls: set[str] = set()
        for entry in feed.entries:
            pub = _parse_published(entry)
            too_old = pub is not None and pub < cutoff
            if too_old:
                continue
            item = _normalize(entry)
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            items.append(item)

        logger.info(
            "arXiv: %d paper(s) in last 24 h (feed returned %d, categories: %s)",
            len(items), len(feed.entries), ", ".join(categories),
        )
        return items

    except Exception as exc:
        logger.error("Failed to fetch arXiv feed: %s", exc)
        return []


def fetch_all() -> list[dict]:
    """Convenience: load config/sources.yaml and call fetch()."""
    config = _load_config()
    return fetch(config.get("arxiv", {}))
