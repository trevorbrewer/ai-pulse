#!/usr/bin/env python3
"""Quick manual test: fetch arXiv papers and print a summary.

Run from the repo root:
    python3 scripts/test_arxiv.py

Optional flags:
    --all-time          disable the 24-hour cutoff
    --max N             override max_results (default: from sources.yaml)
    --category CAT      add or replace categories, e.g. --category cs.AI --category cs.CV
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sources import arxiv

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test arXiv fetcher")
    parser.add_argument("--all-time", action="store_true", help="Remove 24-hour cutoff")
    parser.add_argument("--max", type=int, metavar="N", help="Override max_results")
    parser.add_argument(
        "--category", action="append", metavar="CAT",
        help="Category to fetch (repeatable); overrides config if given",
    )
    args = parser.parse_args()

    config = arxiv._load_config().get("arxiv", {})

    if args.max:
        config["max_results"] = args.max
    if args.category:
        config["category"] = args.category[0]
        config["extra_categories"] = args.category[1:]

    cutoff = datetime(1970, 1, 1, tzinfo=timezone.utc) if args.all_time else None
    if args.all_time:
        print("⚠  --all-time: 24-hour filter disabled\n")

    print(
        f"Querying arXiv — categories: "
        f"{config.get('category')} + {config.get('extra_categories', [])}, "
        f"max_results: {config.get('max_results', 50)}"
    )
    print("─" * 60)

    items = arxiv.fetch(config, cutoff=cutoff)

    if not items:
        print("\nNo papers returned. Try --all-time or adjust --category.")
        sys.exit(0)

    for item in items:
        pub = (item["published"] or "unknown")[:10]
        print(f"\n[{pub}]  {item['title']}")
        print(f"         {item['url']}")
        snippet = item["summary_raw"][:160]
        if snippet:
            print(f"         {snippet}…")

    print(f"\n{'═' * 60}")
    print(f"Total papers: {len(items)}")

    out_path = Path("/tmp/arxiv_test_output.json")
    out_path.write_text(json.dumps(items, indent=2, default=str))
    print(f"Full JSON written to {out_path}")


if __name__ == "__main__":
    main()
