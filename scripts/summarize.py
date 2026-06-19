#!/usr/bin/env python3
"""Read today's raw JSON, call Claude API, write digest paragraphs back to archive."""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional in CI where secrets are real env vars

import anthropic

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

_ARCHIVE_DIR = Path(__file__).parent.parent / "archive" / "daily"
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 8192

_SYSTEM_PROMPT = """\
You are a thoughtful editor for a daily AI news digest aimed at practitioners.

Your task: group today's stories by theme, then write one tight, factual, \
magazine-style paragraph per story.

Rules:
- No hype language and no superlatives ("revolutionary", "groundbreaking", \
"game-changing", "breakthrough") — state facts and let readers draw conclusions.
- No exclamation marks.
- Each paragraph must contain: what happened, why it is notable, and at least \
one concrete detail (a number, a name, a method, a comparison).
- Keep each paragraph to 3-5 sentences.
- Group stories under descriptive theme names (e.g. "Model Releases", \
"Safety Research", "Open-Source Tools", "Industry News", "Research Papers").
- Every story must appear in exactly one theme.
- Output ONLY valid JSON — no preamble, no commentary, no markdown fences.

Required output schema:
[
  {
    "theme": "<descriptive theme name>",
    "stories": [
      {
        "title": "<original story title>",
        "url": "<original story url>",
        "source": "<original source>",
        "paragraph": "<your written paragraph>"
      }
    ]
  }
]"""


def _build_user_message(items: list[dict]) -> str:
    lines = [f"Today's AI news items ({len(items)} total). Write the digest.\n"]
    for i, item in enumerate(items, 1):
        title = (item.get("title") or "(no title)").strip()
        url = (item.get("url") or "").strip()
        source = (item.get("source") or "").strip()
        summary = (item.get("summary_raw") or "")[:400].strip()
        lines.append(f"{i}. [{source}] {title}")
        if url:
            lines.append(f"   URL: {url}")
        if summary:
            lines.append(f"   Summary: {summary}")
        lines.append("")
    return "\n".join(lines)


def run(date_str: str) -> Path:
    raw_path = _ARCHIVE_DIR / f"{date_str}.json"
    if not raw_path.exists():
        logger.error("Raw aggregate file not found: %s — run aggregate.py first", raw_path)
        sys.exit(1)

    payload = json.loads(raw_path.read_text())
    items = payload.get("items", [])

    if not items:
        logger.warning("No items in %s — writing empty digest", raw_path)
        digest: list[dict] = []
    else:
        logger.info("Summarising %d items with %s…", len(items), _MODEL)
        client = anthropic.Anthropic()

        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_message(items)}],
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown code fences if the model added them despite instructions
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```", 2)[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.rsplit("```", 1)[0].strip()

        try:
            digest = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.error(
                "Claude returned non-JSON output (%s)\n\nFirst 500 chars:\n%s",
                exc, raw_text[:500],
            )
            sys.exit(1)

    out_path = _ARCHIVE_DIR / f"{date_str}_digest.json"
    out_path.write_text(json.dumps(digest, indent=2, ensure_ascii=False))

    story_count = sum(len(t.get("stories", [])) for t in digest)
    logger.info(
        "Digest: %d theme(s), %d story(ies) → %s",
        len(digest), story_count, out_path,
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarise today's raw aggregate into a themed digest via Claude"
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
