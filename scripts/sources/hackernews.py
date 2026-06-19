"""Fetch top AI stories from the Hacker News Algolia search API.

Note: HN Algolia returns JSON, not RSS/Atom, so this module uses requests
rather than feedparser. The normalized output schema matches the other sources.
"""

import logging
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
_FETCH_TIMEOUT = 15
_MAX_PAGES = 5       # safety cap — each page is up to 50 hits
_HITS_PER_PAGE = 50


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _hn_url(object_id: str) -> str:
    return f"https://news.ycombinator.com/item?id={object_id}"


def _parse_created_at(hit: dict) -> datetime | None:
    raw = hit.get("created_at")
    if not raw:
        return None
    try:
        # Algolia returns ISO 8601 with a trailing Z
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize(hit: dict) -> dict:
    object_id = hit.get("objectID", "")
    # Regular stories have an external url; Ask/Show HN items don't
    url = hit.get("url") or _hn_url(object_id)
    pub = _parse_created_at(hit)
    # story_text is only present on Ask/Show HN; strip None safely
    summary_raw = (hit.get("story_text") or "").strip()

    return {
        "title": (hit.get("title") or "").strip(),
        "url": url,
        "source": "Hacker News",
        "published": pub.isoformat() if pub else None,
        "summary_raw": summary_raw,
        # Extra fields useful for ranking/display; not in the base schema but
        # downstream scripts can use them if present
        "points": hit.get("points"),
        "num_comments": hit.get("num_comments"),
        "hn_url": _hn_url(object_id),
    }


def _fetch_page(endpoint: str, params: dict, page: int) -> dict | None:
    try:
        resp = requests.get(
            endpoint,
            params={**params, "page": page},
            timeout=_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("HN Algolia request failed (page %d): %s", page, exc)
        return None


def fetch(config: dict, *, cutoff: datetime | None = None) -> list[dict]:
    """Return normalized HN story dicts for the last 24 h meeting min_points.

    config keys used: endpoint, query, min_points, tags
    """
    if cutoff is None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    endpoint = config.get("endpoint", "https://hn.algolia.com/api/v1/search")
    query = config.get("query", "AI")
    min_points = int(config.get("min_points", 0))
    tags = config.get("tags", "story")

    # Push time and points filters to the API so we transfer only relevant hits
    cutoff_ts = math.floor(cutoff.timestamp())
    numeric_filters = f"created_at_i>{cutoff_ts}"
    if min_points:
        numeric_filters += f",points>={min_points}"

    base_params = {
        "query": query,
        "tags": tags,
        "numericFilters": numeric_filters,
        "hitsPerPage": _HITS_PER_PAGE,
    }

    items: list[dict] = []
    seen_ids: set[str] = set()

    for page in range(_MAX_PAGES):
        data = _fetch_page(endpoint, base_params, page)
        if data is None:
            break

        hits = data.get("hits", [])
        nb_pages = data.get("nbPages", 1)

        for hit in hits:
            oid = hit.get("objectID", "")
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
            items.append(_normalize(hit))

        logger.info(
            "HN page %d/%d: %d hits (running total: %d)",
            page + 1, min(nb_pages, _MAX_PAGES), len(hits), len(items),
        )

        if page + 1 >= nb_pages:
            break

    logger.info("Hacker News: %d story(ies) in last 24 h", len(items))
    return items


def fetch_all() -> list[dict]:
    """Convenience: load config/sources.yaml and call fetch()."""
    config = _load_config()
    return fetch(config.get("hackernews", {}))
