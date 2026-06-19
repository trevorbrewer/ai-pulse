#!/usr/bin/env python3
"""Quick manual test: fetch HN stories and print a summary.

Run from the repo root:
    python3 scripts/test_hackernews.py

Optional flags:
    --all-time          disable the 24-hour cutoff
    --min-points N      override min_points from config (default: config value)
    --query TEXT        override the search query
    --max-pages N       cap how many Algolia pages to fetch (default: 5)
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sources import hackernews

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test HN Algolia fetcher")
    parser.add_argument("--all-time", action="store_true", help="Remove 24-hour cutoff")
    parser.add_argument("--min-points", type=int, metavar="N", help="Override min_points")
    parser.add_argument("--query", metavar="TEXT", help="Override search query")
    parser.add_argument("--max-pages", type=int, metavar="N", help="Cap pages fetched")
    args = parser.parse_args()

    config = hackernews._load_config().get("hackernews", {})

    if args.min_points is not None:
        config["min_points"] = args.min_points
    if args.query:
        config["query"] = args.query
    if args.max_pages:
        hackernews._MAX_PAGES = args.max_pages

    cutoff = datetime(1970, 1, 1, tzinfo=timezone.utc) if args.all_time else None
    if args.all_time:
        print("⚠  --all-time: 24-hour filter disabled\n")

    print(
        f"Querying HN Algolia — "
        f"query: \"{config.get('query')}\", "
        f"min_points: {config.get('min_points', 0)}, "
        f"tags: {config.get('tags', 'story')}"
    )
    print("─" * 60)

    items = hackernews.fetch(config, cutoff=cutoff)

    if not items:
        print("\nNo stories returned. Try --all-time or lower --min-points.")
        sys.exit(0)

    # Sort by points descending for display
    items_sorted = sorted(items, key=lambda x: x.get("points") or 0, reverse=True)

    for item in items_sorted:
        pub = (item["published"] or "unknown")[:16].replace("T", " ")
        pts = item.get("points", "?")
        cmt = item.get("num_comments", "?")
        print(f"\n[{pub}]  {pts} pts  {cmt} comments")
        print(f"  {item['title']}")
        print(f"  {item['url']}")
        if item.get("hn_url") != item["url"]:
            print(f"  HN: {item['hn_url']}")
        if item["summary_raw"]:
            snippet = item["summary_raw"][:160]
            print(f"  {snippet}…")

    print(f"\n{'═' * 60}")
    print(f"Total stories: {len(items)}")

    out_path = Path("/tmp/hn_test_output.json")
    out_path.write_text(json.dumps(items, indent=2, default=str))
    print(f"Full JSON written to {out_path}")


if __name__ == "__main__":
    main()
