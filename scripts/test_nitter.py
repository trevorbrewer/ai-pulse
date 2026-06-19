#!/usr/bin/env python3
"""Quick manual test: fetch tweets via Nitter RSS and print a summary.

Run from the repo root:
    python3 scripts/test_nitter.py

Because nitter instances are unreliable this script also reports which
instance responded (or failed) for each handle, making it easy to spot
dead instances and update nitter.instances in config/sources.yaml.

Optional flags:
    --all-time          disable the 24-hour cutoff
    --handle HANDLE     only test this Twitter handle (repeatable)
    --instance HOST     prepend a custom nitter instance hostname to try first
    --probe-instances   just check which instances are reachable, then exit
"""

import argparse
import json
import logging
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sources import nitter

# Show DEBUG so instance fallthrough is visible
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")


def _probe_instances(instances: list[str], handle: str = "karpathy") -> None:
    """Print a reachability table for every configured instance."""
    print(f"Probing {len(instances)} instance(s) with @{handle}...\n")
    col = max((len(i) for i in instances), default=10) + 2
    print(f"  {'Instance':<{col}}  Status   Entries  Live?")
    print(f"  {'─' * col}  ───────  ───────  ─────")
    for instance in instances:
        url = f"https://{instance}/{handle}/rss"
        try:
            old = socket.getdefaulttimeout()
            socket.setdefaulttimeout(nitter._FETCH_TIMEOUT)
            try:
                feed = feedparser.parse(url)
            finally:
                socket.setdefaulttimeout(old)
            status = getattr(feed, "status", "?")
            entries = len(feed.entries)
            live = nitter._try_parse_feed(url) is not None
            mark = "✓" if live else "✗"
            print(f"  {instance:<{col}}  {str(status):<7}  {entries:<7}  {mark}")
        except Exception as exc:
            print(f"  {instance:<{col}}  ERR      —        ✗  ({exc})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Nitter RSS fetcher")
    parser.add_argument("--all-time", action="store_true", help="Remove 24-hour cutoff")
    parser.add_argument(
        "--handle", action="append", metavar="HANDLE",
        help="Only fetch this handle (repeatable); overrides config people list",
    )
    parser.add_argument(
        "--instance", action="append", metavar="HOST",
        help="Prepend a custom nitter instance hostname to try first",
    )
    parser.add_argument(
        "--probe-instances", action="store_true",
        help="Check which nitter instances are reachable, then exit",
    )
    args = parser.parse_args()

    config = nitter._load_config()
    instances: list[str] = config.get("nitter", {}).get("instances", [])
    if args.instance:
        instances = args.instance + instances

    if args.probe_instances:
        probe_handle = (args.handle or ["karpathy"])[0]
        _probe_instances(instances, probe_handle)
        sys.exit(0)

    if args.handle:
        people = [{"name": h, "twitter": h} for h in args.handle]
    else:
        people = [p for p in config.get("people", []) if p.get("twitter")]

    if not people:
        print("No people with Twitter handles found. Check config/sources.yaml or --handle.")
        sys.exit(1)

    cutoff = datetime(1970, 1, 1, tzinfo=timezone.utc) if args.all_time else None
    if args.all_time:
        print("⚠  --all-time: 24-hour filter disabled\n")

    handles = [p.get("twitter") for p in people]
    print(f"Testing {len(people)} handle(s): {', '.join(f'@{h}' for h in handles)}")
    print(f"Nitter instances (priority order): {instances}")
    print("─" * 60)

    items = nitter.fetch(people, instances, cutoff=cutoff)

    if not items:
        print(
            "\nNo results. Nitter instances may all be down (blog fallbacks also empty).\n"
            "Run --probe-instances to check reachability.\n"
            "Run --all-time to rule out the time filter."
        )
        sys.exit(0)

    by_source: dict[str, list[dict]] = {}
    for item in items:
        by_source.setdefault(item["source"], []).append(item)

    for source, entries in sorted(by_source.items()):
        print(f"\n{'─' * 60}")
        print(f"  {source}  ({len(entries)} item(s))")
        print(f"{'─' * 60}")
        for e in entries:
            pub = (e["published"] or "unknown")[:16].replace("T", " ")
            print(f"  [{pub}]  {e['url']}")
            snippet = (e["summary_raw"] or e["title"])[:160].replace("\n", " ")
            if snippet:
                print(f"             {snippet}")

    print(f"\n{'═' * 60}")
    print(f"Total items: {len(items)} across {len(by_source)} source(s)")

    out_path = Path("/tmp/nitter_test_output.json")
    out_path.write_text(json.dumps(items, indent=2, default=str))
    print(f"Full JSON written to {out_path}")


if __name__ == "__main__":
    main()
