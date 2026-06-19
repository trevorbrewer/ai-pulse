#!/usr/bin/env python3
"""Read the last 7 days of daily digests and synthesise weekly themes, a project idea, and a discussion question."""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional in CI where secrets are real env vars

import anthropic

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

_DAILY_DIR = Path(__file__).parent.parent / "archive" / "daily"
_WEEKLY_DIR = Path(__file__).parent.parent / "archive" / "weekly"
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096

_SYSTEM_PROMPT = """\
You are an editor writing a weekly synthesis of AI news for practitioners.

You will receive themed story blocks from the past week's daily digests. \
Produce a JSON object with three top-level keys: "themes", "project_idea", \
and "discussion_question".

"themes": The 3-4 most significant recurring themes or developments of the week.
  For each theme:
  - "name": a short descriptive label
  - "summary": a 3-5 sentence narrative — what the week's coverage reveals as a \
whole, not just a list of events
  - "key_stories": 2-3 representative references as [{"title": "...", "url": "..."}]
  Order themes by significance (most important first).

"project_idea": One concrete, buildable project sized for a few hours to a weekend \
that directly practices skills from the week's most important theme.
  - Be specific: name a dataset, API, library, or technique to use
  - Require no special hardware or paid services beyond free tiers
  Fields: title, theme (which theme it builds on), description (2-3 sentences), \
estimated_time ("a few hours" / "a weekend"), skills_practiced (list of 3-5 strings).

"discussion_question": One substantive, open-ended technical question worth sitting \
with — something curious practitioners might want to think through or explore in \
depth. Not a trivia question; should have no single obvious answer.
  Fields: question (the question itself), context (1-2 sentences on why it matters).

Rules:
- No hype language, no superlatives ("revolutionary", "groundbreaking", \
"game-changing"), no exclamation marks.
- Write factually; let readers draw conclusions.
- Output ONLY valid JSON — no preamble, no commentary, no markdown fences.

Required output schema:
{
  "themes": [
    {
      "name": "<theme name>",
      "summary": "<narrative paragraph>",
      "key_stories": [{"title": "...", "url": "..."}]
    }
  ],
  "project_idea": {
    "title": "...",
    "theme": "<which theme this builds on>",
    "description": "...",
    "estimated_time": "...",
    "skills_practiced": ["..."]
  },
  "discussion_question": {
    "question": "...",
    "context": "..."
  }
}"""


def _load_week_digests(week_ending: datetime) -> tuple[list[dict], list[str]]:
    """Return (all_theme_blocks_across_week, dates_included) in chronological order."""
    all_themes: list[dict] = []
    dates_included: list[str] = []

    for days_ago in range(6, -1, -1):  # oldest first
        date = (week_ending - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        path = _DAILY_DIR / f"{date}_digest.json"
        if not path.exists():
            logger.debug("No digest for %s — skipping", date)
            continue
        try:
            blocks = json.loads(path.read_text())
            if blocks:
                all_themes.extend(blocks)
                dates_included.append(date)
                logger.info("Loaded %d theme block(s) from %s", len(blocks), date)
        except Exception as exc:
            logger.warning("Could not read %s: %s", path, exc)

    return all_themes, dates_included


def _build_user_message(theme_blocks: list[dict], dates: list[str]) -> str:
    lines = [
        f"Week of {dates[0]} through {dates[-1]} ({len(dates)} day(s) of coverage).\n",
        "Produce the weekly synthesis.\n",
    ]

    for block in theme_blocks:
        theme_name = (block.get("theme") or "Uncategorized").strip()
        lines.append(f"### {theme_name}")
        for story in block.get("stories", []):
            title = (story.get("title") or "").strip()
            url = (story.get("url") or "").strip()
            paragraph = (story.get("paragraph") or "").strip()
            line = f"- {title}"
            if url:
                line += f"  ({url})"
            lines.append(line)
            if paragraph:
                lines.append(f"  {paragraph[:300]}")
        lines.append("")

    return "\n".join(lines)


def run(week_ending_str: str) -> Path:
    week_ending = datetime.strptime(week_ending_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    theme_blocks, dates_included = _load_week_digests(week_ending)

    if not dates_included:
        logger.error(
            "No daily digests found for the week ending %s. "
            "Run summarize.py for at least one day first.",
            week_ending_str,
        )
        sys.exit(1)

    if len(dates_included) < 3:
        logger.warning("Only %d day(s) of data — synthesis will be limited", len(dates_included))

    logger.info(
        "Synthesising %d theme block(s) from %d day(s) with %s…",
        len(theme_blocks), len(dates_included), _MODEL,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(theme_blocks, dates_included)}],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown fences defensively
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```", 2)[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.rsplit("```", 1)[0].strip()

    try:
        synthesis = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error(
            "Claude returned non-JSON output (%s)\n\nFirst 500 chars:\n%s",
            exc, raw_text[:500],
        )
        sys.exit(1)

    _WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _WEEKLY_DIR / f"{week_ending_str}.json"

    out_path.write_text(json.dumps({
        "week_ending": week_ending_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days_included": dates_included,
        **synthesis,
    }, indent=2, ensure_ascii=False))

    logger.info(
        "Weekly synthesis: %d theme(s) → %s",
        len(synthesis.get("themes", [])), out_path,
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthesise a week of daily digests into themes, a project idea, and a discussion question"
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Week-ending date YYYY-MM-DD (default: today UTC)",
    )
    args = parser.parse_args()
    run(args.date)


if __name__ == "__main__":
    main()
