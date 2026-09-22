#!/usr/bin/env python3
"""Fetch latest RSS headlines for the Homarr dashboard news panel.

No summarization — just raw headlines from configured feeds.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser

# --- Configuration ---

FEEDS = [
    {"name": "Semiconductor Engineering", "url": "https://semiengineering.com/feed/", "category": "Semiconductor"},
    {"name": "Chips and Cheese", "url": "https://chipsandcheese.com/feed", "category": "Semiconductor"},
    {"name": "IEEE Spectrum AI", "url": "https://spectrum.ieee.org/rss/artificial-intelligence", "category": "AI"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/10000664/device/rss", "category": "Stocks"},
]

DB_PATH = Path("/home/juyoungoh/dashcraft/data/dashboard.db")
MAX_ITEMS = 12  # total headlines in the panel
ROUNDUP_PATTERNS = [
    r"weekly\s*review",
    r"week\s*in\s*review",
    r"roundup",
    r"digest",
    r"wrap-up",
    r"chip\s*industry\s*week",
]


def is_roundup(title: str) -> bool:
    """Skip roundups/digests that aren't useful as individual headlines."""
    lower = title.lower()
    return any(re.search(pat, lower) for pat in ROUNDUP_PATTERNS)


def strip_html(text: str) -> str:
    """Strip HTML tags from description text."""
    clean = re.sub(r"<[^>]+>", " ", text)
    return clean.strip()


def truncate(text: str, max_len: int = 200) -> str:
    """Truncate description to a readable snippet."""
    text = strip_html(text)
    if len(text) <= max_len:
        return text
    cutoff = text[:max_len].rsplit(".", 1)[0]
    return cutoff.strip() + "." if cutoff else text[:max_len] + "…"


def fetch_all() -> list[dict]:
    """Fetch all RSS items, deduplicated and filtered."""
    seen_titles: set[str] = set()
    all_items: list[dict] = []

    for feed_cfg in FEEDS:
        try:
            feed = feedparser.parse(feed_cfg["url"])
        except Exception as e:
            print(f"WARNING: failed to fetch {feed_cfg['name']}: {e}", file=sys.stderr)
            continue

        for entry in feed.entries[:30]:
            title = entry.get("title", "").strip()
            if not title or is_roundup(title) or title in seen_titles:
                continue

            seen_titles.add(title)

            published = ""
            raw_date = entry.get("published_parsed") or entry.get("created_parsed")
            if raw_date:
                try:
                    dt = parsedate_to_datetime(entry.get("published", ""))
                    published = dt.isoformat()
                except Exception:
                    pass

            all_items.append({
                "id": entry.get("id", "") or entry.get("link", ""),
                "category": feed_cfg["category"],
                "source": feed_cfg["name"],
                "title": title,
                "url": entry.get("link", ""),
                "description": truncate(entry.get("summary", "") or entry.get("description", "")),
                "summary": truncate(entry.get("summary", "") or entry.get("description", "")),
                "published_at": published,
            })

    # Sort by published date descending, fallback to insertion order
    all_items.sort(key=lambda x: x["published_at"] or "", reverse=True)
    return all_items


def init_db(conn: sqlite3.Connection) -> None:
    """Ensure newsletter_brief table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS newsletter_brief (
            id INTEGER PRIMARY KEY,
            generated_at TEXT,
            source TEXT,
            headline TEXT,
            items_json TEXT
        )
    """)


def save(conn: sqlite3.Connection, items: list[dict]) -> None:
    """Save headlines as the latest brief entry."""
    conn.execute("DELETE FROM newsletter_brief")
    conn.execute("INSERT INTO newsletter_brief (id, generated_at, source, headline, items_json) VALUES (1, ?, ?, ?, ?)",
                 (datetime.now(timezone.utc).isoformat(), "rss", "Latest News", json.dumps(items)))
    conn.commit()


def run_cycle() -> dict:
    """Run one news fetch cycle and return a status report for the API endpoint."""
    errors = []
    items = fetch_all()
    items = items[:MAX_ITEMS]

    conn = sqlite3.connect(str(DB_PATH))
    try:
        init_db(conn)
        save(conn, items)
    except Exception as exc:
        errors.append(f"DB save failed: {exc}")
    finally:
        conn.close()

    # --- Export to Homarr news.json ---
    news_json = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "rss",
        "headline": "Latest News",
        "items": items,
    }
    news_path = Path("/DATA/nas/Projects/homarr/news/news.json")
    try:
        news_path.parent.mkdir(parents=True, exist_ok=True)
        news_path.write_text(json.dumps(news_json, indent=2))
    except Exception as exc:
        errors.append(f"news.json write failed: {exc}")

    return {"fetched_new": len(items), "errors": errors, "items": items}


def main() -> int:
    print("Fetching RSS headlines...")
    items = fetch_all()

    # Keep only top N items
    items = items[:MAX_ITEMS]

    if not items:
        print("ERROR: no items fetched", file=sys.stderr)
        return 1

    print(f"Got {len(items)} headlines")
    for item in items:
        print(f"  [{item['category']}] {item['title'][:70]}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        init_db(conn)
        save(conn, items)
    finally:
        conn.close()

    # --- Export to Homarr news.json ---
    news_json = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "rss",
        "headline": "Latest News",
        "items": items,
    }

    news_path = Path("/DATA/nas/Projects/homarr/news/news.json")
    news_path.parent.mkdir(parents=True, exist_ok=True)
    news_path.write_text(json.dumps(news_json, indent=2))
    print(f"Wrote {len(items)} items to {news_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
