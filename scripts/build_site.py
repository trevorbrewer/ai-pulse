#!/usr/bin/env python3
"""Render archive JSON into HTML pages under docs/ via Jinja2 templates."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_ARCHIVE_DAILY = _ROOT / "archive" / "daily"
_ARCHIVE_WEEKLY = _ROOT / "archive" / "weekly"
_DOCS = _ROOT / "docs"
_TEMPLATES = _ROOT / "templates"

# ── stylesheet ────────────────────────────────────────────────────────────────

_CSS = """\
*, *::before, *::after { box-sizing: border-box; }

:root {
  --text:    #1a1a1a;
  --muted:   #666;
  --border:  #ddd;
  --accent:  #0969da;
  --visited: #6639ba;
  --surface: #f6f8fa;
  --max-w:   680px;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 1rem;
  line-height: 1.65;
  color: var(--text);
  background: #fff;
  margin: 0;
  padding: 0 1rem 3rem;
}

.container { max-width: var(--max-w); margin: 0 auto; }

/* header */
header {
  border-bottom: 2px solid var(--text);
  padding: 1.5rem 0 0.8rem;
  margin-bottom: 2rem;
}
header h1   { margin: 0 0 0.1rem; font-size: 1.6rem; letter-spacing: -0.02em; }
.tagline    { margin: 0; color: var(--muted); font-size: 0.9rem; }

nav { margin-top: 0.5rem; font-size: 0.875rem; }
nav a { color: var(--muted); text-decoration: none; }
nav a:hover { color: var(--accent); }

/* page title */
.page-title { font-size: 1.4rem; margin: 0 0 1.5rem; }

/* headings */
h2 {
  font-size: 1.05rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin: 2.5rem 0 0.75rem;
  padding-bottom: 0.3rem;
  border-bottom: 1px solid var(--border);
}
h3 { font-size: 1rem; margin: 1.5rem 0 0.3rem; }

/* links */
a         { color: var(--accent); }
a:visited { color: var(--visited); }

/* source badge */
.source {
  display: inline-block;
  font-size: 0.7rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0 0.35em;
  margin-left: 0.45em;
  vertical-align: middle;
  color: var(--muted);
  white-space: nowrap;
  font-weight: 400;
}

/* daily story blocks */
.theme-section  { margin-bottom: 2rem; }
.story          { margin: 1.25rem 0; }
.story-title    { font-weight: 600; margin: 0 0 0.3rem; font-size: 0.975rem; }
.story p        { margin: 0; }

/* index list */
.digest-list {
  list-style: none;
  padding: 0; margin: 0;
}
.digest-list li {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1rem;
  padding: 0.55rem 0;
  border-bottom: 1px solid var(--border);
}
.digest-list li:last-child { border-bottom: none; }
.digest-list .label { font-weight: 500; }
.digest-list .meta  { font-size: 0.85rem; color: var(--muted); white-space: nowrap; }

/* weekly */
.days-covered { margin: -1rem 0 1.5rem; font-size: 0.85rem; color: var(--muted); }

.theme-block { margin-bottom: 1.75rem; }

.key-stories {
  margin: 0.5rem 0 0;
  padding-left: 1.2rem;
}
.key-stories li { margin-bottom: 0.2rem; font-size: 0.9rem; }

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem 1.25rem;
  margin-top: 0.5rem;
}
.card h3       { margin: 0 0 0.5rem; font-size: 1rem; }
.card p        { margin: 0 0 0.5rem; }
.card p:last-child { margin-bottom: 0; }
.meta-line     { font-size: 0.875rem; color: var(--muted); }
.discussion-q  { font-weight: 600; font-size: 1rem; }

.skill-tags {
  display: flex; flex-wrap: wrap; gap: 0.35rem;
  list-style: none; padding: 0; margin: 0.6rem 0 0;
}
.skill-tags li {
  background: #dbeafe;
  color: #1e40af;
  border-radius: 3px;
  padding: 0.1em 0.55em;
  font-size: 0.78rem;
}

/* misc */
.empty  { color: var(--muted); font-style: italic; }

footer {
  margin-top: 3rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
  font-size: 0.8rem;
  color: var(--muted);
}

@media (max-width: 480px) {
  body { font-size: 0.95rem; padding: 0 0.75rem 2rem; }
  .digest-list li { flex-direction: column; gap: 0.1rem; }
  .digest-list .meta { font-size: 0.8rem; }
}
"""

# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    """2026-06-19 → Thursday, June 19, 2026"""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    except ValueError:
        return date_str


def _load_daily_digests() -> list[dict]:
    """Return all *_digest.json entries sorted newest-first."""
    results = []
    for path in sorted(_ARCHIVE_DAILY.glob("*_digest.json"), reverse=True):
        date = path.stem.replace("_digest", "")
        try:
            digest = json.loads(path.read_text())
            results.append({
                "date": date,
                "date_fmt": _fmt_date(date),
                "digest": digest,
            })
        except Exception as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
    return results


def _load_weekly_syntheses() -> list[dict]:
    """Return all weekly/*.json entries sorted newest-first, skipping placeholders."""
    results = []
    for path in sorted(_ARCHIVE_WEEKLY.glob("*.json"), reverse=True):
        date = path.stem
        try:
            data = json.loads(path.read_text())
            if not data.get("themes"):  # skip empty placeholders
                continue
            data.pop("generated_at", None)  # overridden at render time
            results.append({
                "date": date,
                "date_fmt": _fmt_date(date),
                **data,
            })
        except Exception as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
    return results


# ── main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["fmt_date"] = _fmt_date

    daily = _load_daily_digests()
    weekly = _load_weekly_syntheses()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # stylesheet
    (_DOCS / "style.css").write_text(_CSS)

    # daily pages
    (_DOCS / "daily").mkdir(parents=True, exist_ok=True)
    tmpl_day = env.get_template("day.html.jinja")
    for entry in daily:
        out = _DOCS / "daily" / f"{entry['date']}.html"
        out.write_text(tmpl_day.render(**entry, generated_at=now))
    logger.info("Built %d daily page(s)", len(daily))

    # weekly pages
    (_DOCS / "weekly").mkdir(parents=True, exist_ok=True)
    tmpl_week = env.get_template("week.html.jinja")
    for entry in weekly:
        out = _DOCS / "weekly" / f"{entry['date']}.html"
        out.write_text(tmpl_week.render(**entry, generated_at=now))
    logger.info("Built %d weekly page(s)", len(weekly))

    # index — always regenerated
    tmpl_index = env.get_template("index.html.jinja")
    (_DOCS / "index.html").write_text(
        tmpl_index.render(daily=daily, weekly=weekly, generated_at=now)
    )
    logger.info("Built docs/index.html  (%d daily, %d weekly)", len(daily), len(weekly))


if __name__ == "__main__":
    run()
