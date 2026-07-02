#!/usr/bin/env python3
"""Orchestrate all source fetchers and write a dated raw JSON file to archive/daily/."""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sources import arxiv, hackernews, nitter, rss_feeds

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

_ARCHIVE_DIR = Path(__file__).parent.parent / "archive" / "daily"


def run(date_str: str) -> Path:
    logger.info("Fetching RSS/blog feeds…")
    rss_items = rss_feeds.fetch_all()

    logger.info("Fetching arXiv papers…")
    arxiv_items = arxiv.fetch_all()

    logger.info("Fetching Hacker News stories…")
    hn_items = hackernews.fetch_all()

    logger.info("Fetching Twitter/Nitter feeds…")
    nitter_items = nitter.fetch_all()

    # Merge and deduplicate by URL (keep first occurrence per source priority order)
    all_items = rss_items + arxiv_items + hn_items + nitter_items
    seen_urls: dict = {}
    no_url_items: list[dict] = []
    for item in all_items:
        url = (item.get("url") or "").strip()
        if url:
            if url not in seen_urls:
                seen_urls[url] = item
        else:
            no_url_items.append(item)

    deduped = list(seen_urls.values()) + no_url_items

    deduped.sort(key=lambda x: x.get("published") or "", reverse=True)
    items = deduped[:25]

    source_counts = {
        "rss_feeds": len(rss_items),
        "arxiv": len(arxiv_items),
        "hackernews": len(hn_items),
        "nitter": len(nitter_items),
        "total_raw": len(all_items),
        "total_deduped": len(deduped),
        "total_saved": len(items),
    }

    logger.info(
        "Fetched %d items total, %d after dedup, %d saved  (rss: %d, arxiv: %d, hn: %d, nitter: %d)",
        len(all_items), len(deduped), len(items),
        len(rss_items), len(arxiv_items), len(hn_items), len(nitter_items),
    )

    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _ARCHIVE_DIR / f"{date_str}.json"

    out_path.write_text(json.dumps({
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_counts": source_counts,
        "items": items,
    }, indent=2, default=str))

    logger.info("Wrote %d items to %s", len(items), out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate all AI news sources into a dated JSON file"
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Target date YYYY-MM-DD (default: today UTC)",
    )
    args = parser.parse_args()
    run(args.date)


if __name__ == "__main__":
    main()
