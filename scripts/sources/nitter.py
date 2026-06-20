"""Fetch recent tweets via Nitter RSS — best-effort, never raises.

For each person with a twitter handle, tries every Nitter instance from
config in order and accepts the first live response.  If every instance
fails, falls back to the person's blog_rss (if set) so the daily run
still gets some signal for them.  If that also fails, the person is
silently skipped.  This module must never raise — daily.yml must succeed
even if Nitter is completely dead.

Keep the instance list fresh:
  https://github.com/zedeus/nitter/wiki/Instances
"""

import logging
import re
import socket
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
_FETCH_TIMEOUT = 10  # short per attempt — we fall through to the next instance


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── low-level helpers ────────────────────────────────────────────────────────

def _parse_published(entry: feedparser.FeedParserDict) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def _try_parse_feed(url: str) -> feedparser.FeedParserDict | None:
    """Return a parsed feed, or None on any error / bad HTTP status / empty bozo."""
    try:
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_FETCH_TIMEOUT)
        try:
            feed = feedparser.parse(url)
        finally:
            socket.setdefaulttimeout(old)
        if getattr(feed, "status", 200) >= 400:
            return None
        if feed.bozo and not feed.entries:
            return None
        return feed
    except Exception:
        return None


def _filter_since(feed: feedparser.FeedParserDict, cutoff: datetime) -> list:
    return [
        e for e in feed.entries
        if (pub := _parse_published(e)) is None or pub >= cutoff
    ]


# ── normalisers ──────────────────────────────────────────────────────────────

def _normalize_tweet(entry: feedparser.FeedParserDict, handle: str, person_name: str) -> dict:
    raw_url = getattr(entry, "link", "").strip()
    m = re.search(r"/status/(\d+)", raw_url)
    tweet_url = f"https://twitter.com/{handle}/status/{m.group(1)}" if m else raw_url
    pub = _parse_published(entry)
    return {
        "title": _strip_html(getattr(entry, "title", "")).strip(),
        "url": tweet_url,
        "source": f"Twitter/@{handle}",
        "published": pub.isoformat() if pub else None,
        "summary_raw": _strip_html(getattr(entry, "summary", None) or ""),
        "person": person_name,
    }


def _normalize_blog(entry: feedparser.FeedParserDict, person_name: str) -> dict:
    pub = _parse_published(entry)
    summary = _strip_html(
        getattr(entry, "summary", None) or getattr(entry, "description", None) or ""
    )
    return {
        "title": getattr(entry, "title", "").strip(),
        "url": getattr(entry, "link", "").strip(),
        "source": f"blog/{person_name}",
        "published": pub.isoformat() if pub else None,
        "summary_raw": summary,
        "person": person_name,
    }


# ── per-person fetchers ──────────────────────────────────────────────────────

def _fetch_nitter(handle: str, person_name: str, instances: list[str], cutoff: datetime) -> list[dict] | None:
    """Try each instance; return items on first success, None if all fail.

    Returns None (not []) on total failure so the caller knows to try the
    blog fallback — an empty list means "succeeded but nothing recent".
    """
    for instance in instances:
        url = f"https://{instance}/{handle}/rss"
        feed = _try_parse_feed(url)
        if feed is None:
            logger.debug("Nitter %s failed for @%s", instance, handle)
            continue
        entries = _filter_since(feed, cutoff)
        items = [_normalize_tweet(e, handle, person_name) for e in entries]
        logger.info("@%s via %s: %d tweet(s) in last 24 h (feed had %d total)",
                    handle, instance, len(items), len(feed.entries))
        return items

    return None  # every instance failed


def _fetch_blog_fallback(person_name: str, blog_url: str, cutoff: datetime) -> list[dict]:
    """Pull blog_rss as a substitute when Nitter is unavailable for this person."""
    feed = _try_parse_feed(blog_url)
    if feed is None:
        logger.debug("Blog fallback also failed for %s (%s)", person_name, blog_url)
        return []
    entries = _filter_since(feed, cutoff)
    items = [_normalize_blog(e, person_name) for e in entries]
    logger.info("%s blog fallback: %d item(s) in last 24 h", person_name, len(items))
    return items


# ── HTML news scraper fallback ───────────────────────────────────────────────

_SCRAPE_TIMEOUT = 15
_MONTH_FMT = "%b %d, %Y"    # "Jun 17, 2026"


def _fetch_html_news(person_name: str, url: str, cutoff: datetime) -> list[dict]:
    """Scrape an HTML news index page as a last-resort fallback when nitter fails.

    Understands the anthropic.com/news layout (FeaturedGrid and PublicationList
    components), but degrades gracefully on any other structure: it looks for
    <a href> tags containing a <time> element and either a heading or a <span>
    whose class includes the word "title".  Unknown layouts produce an empty list.
    Never raises.
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ai-pulse/1.0)"},
            timeout=_SCRAPE_TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        seen_urls: set[str] = set()
        items: list[dict] = []

        for a in soup.find_all("a", href=re.compile(r"^/news/")):
            # ── date ──────────────────────────────────────────────────────────
            time_el = a.find("time")
            pub: datetime | None = None
            if time_el:
                try:
                    pub = datetime.strptime(
                        time_el.get_text(strip=True), _MONTH_FMT
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

            if pub is not None and pub < cutoff:
                continue

            # ── title ─────────────────────────────────────────────────────────
            heading = a.find(["h2", "h3", "h4", "h5", "h6"])
            if heading:
                title = heading.get_text(strip=True)
            else:
                # PublicationList pattern: title is the last <span> inside the anchor
                # (preceded by a category <span> and a <time>)
                spans = a.find_all("span", recursive=True)
                title = spans[-1].get_text(strip=True) if spans else ""

            if not title:
                continue

            full_url = f"https://www.anthropic.com{a['href']}"
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            items.append({
                "title": title,
                "url": full_url,
                "source": f"Anthropic News",
                "published": pub.isoformat() if pub else None,
                "summary_raw": "",
                "person": person_name,
            })

        logger.info(
            "%s HTML scrape (%s): %d item(s) in last 24 h", person_name, url, len(items)
        )
        return items

    except Exception as exc:
        logger.warning("HTML news scrape failed for %s (%s): %s", person_name, url, exc)
        return []


# ── public API ───────────────────────────────────────────────────────────────

def fetch(people: list[dict], instances: list[str], *, cutoff: datetime | None = None) -> list[dict]:
    """Return tweet/blog dicts for people; never raises."""
    if cutoff is None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    results: list[dict] = []
    for person in people:
        try:
            handle = (person.get("twitter") or "").strip()
            name = person.get("name", handle or "unknown")
            blog_url = (person.get("blog_rss") or "").strip()

            if not handle:
                continue

            nitter_items = _fetch_nitter(handle, name, instances, cutoff)

            if nitter_items is None:
                news_url = (person.get("news_url") or "").strip()
                has_fallback = bool(news_url or blog_url)
                logger.warning("All Nitter instances failed for @%s%s",
                               handle, "; trying fallback" if has_fallback else "")
                if news_url:
                    results.extend(_fetch_html_news(name, news_url, cutoff))
                elif blog_url:
                    results.extend(_fetch_blog_fallback(name, blog_url, cutoff))
            else:
                results.extend(nitter_items)

        except Exception as exc:
            logger.warning("Unexpected error for person %s: %s", person.get("name", "?"), exc)

    return results


def fetch_all() -> list[dict]:
    """Convenience: load config/sources.yaml and call fetch(). Never raises."""
    try:
        config = _load_config()
        people = config.get("people", [])
        instances = config.get("nitter", {}).get("instances", [])
        if not instances:
            logger.warning("No Nitter instances configured in sources.yaml — skipping Nitter")
        return fetch(people, instances)
    except Exception as exc:
        logger.warning("nitter.fetch_all failed entirely: %s", exc)
        return []
