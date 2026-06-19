#!/usr/bin/env python3
"""Quick manual test: fetch all RSS feeds and print a summary.

Run from the repo root:
    python scripts/test_rss_feeds.py

Optional flags:
    --all-time   disable the 24-hour cutoff (useful when feeds have been quiet)
    --source NAME  only fetch feeds whose name contains NAME (case-insensitive)
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Make sure the repo root is on the path so imports resolve
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sources import rss_feeds

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test RSS feed fetcher")
    parser.add_argument(
        "--all-time",
        action="store_true",
        help="Remove 24-hour cutoff — return all entries in each feed",
    )
    parser.add_argument(
        "--source",
        metavar="NAME",
        help="Only test feeds whose name contains NAME (case-insensitive)",
    )
    args = parser.parse_args()

    config = rss_feeds._load_config()
    blog_sources = config.get("blogs", [])
    people_sources = [
        {"name": p["name"], "url": p["blog_rss"]}
        for p in config.get("people", [])
        if p.get("blog_rss")
    ]
    all_sources = blog_sources + people_sources

    if args.source:
        needle = args.source.lower()
        all_sources = [s for s in all_sources if needle in s["name"].lower()]
        if not all_sources:
            print(f"No feeds match --source '{args.source}'")
            sys.exit(1)

    if args.all_time:
        # Monkey-patch cutoff to epoch so nothing is filtered
        _real_fetch_feed = rss_feeds._fetch_feed
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

        def _patched(name, url, cutoff):
            return _real_fetch_feed(name, url, epoch)

        rss_feeds._fetch_feed = _patched
        print("⚠  --all-time: 24-hour filter disabled\n")

    print(f"Testing {len(all_sources)} feed(s)...\n{'─' * 60}")
    items = rss_feeds.fetch(all_sources)

    if not items:
        print("\nNo items returned. Try --all-time if feeds have been quiet.")
        sys.exit(0)

    # Group by source for a readable summary
    by_source: dict[str, list[dict]] = {}
    for item in items:
        by_source.setdefault(item["source"], []).append(item)

    for source, entries in sorted(by_source.items()):
        print(f"\n{'─' * 60}")
        print(f"  {source}  ({len(entries)} item(s))")
        print(f"{'─' * 60}")
        for e in entries:
            pub = e["published"] or "unknown date"
            title = e["title"] or "(no title)"
            url = e["url"] or "(no url)"
            snippet = e["summary_raw"][:120].replace("\n", " ")
            print(f"  [{pub[:10]}]  {title}")
            print(f"             {url}")
            if snippet:
                print(f"             {snippet}…")

    print(f"\n{'═' * 60}")
    print(f"Total items: {len(items)} across {len(by_source)} source(s)")

    # Dump full JSON to a temp file for inspection
    out_path = Path("/tmp/rss_test_output.json")
    out_path.write_text(json.dumps(items, indent=2, default=str))
    print(f"Full JSON written to {out_path}")


if __name__ == "__main__":
    main()
