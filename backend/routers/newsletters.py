from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser
from fastapi import APIRouter, HTTPException, Query

from backend.database import PROJECT_DIR, get_connection

router = APIRouter(prefix="/api/newsletters", tags=["newsletters"])
CONFIG_PATH = PROJECT_DIR / "dashboard.config.json"


def _ensure_table() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS newsletter_item (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT,
                summary TEXT,
                read INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _sources() -> list[dict]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    return cfg.get("newsletter_sources", [])


def _item_id(source: str, url: str, title: str) -> str:
    return hashlib.sha1(f"{source}|{url}|{title}".encode()).hexdigest()[:20]


def _summary(entry) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    text = re.sub(r"<[^>]+>", " ", str(raw))
    text = " ".join(text.replace("\n", " ").split())
    return text[:500] + ("…" if len(text) > 500 else "")


@router.get("/sources")
def sources() -> list[dict]:
    return _sources()


@router.get("")
def list_items(source: str | None = Query(default=None)) -> list[dict]:
    _ensure_table()
    with get_connection() as conn:
        if source:
            rows = conn.execute("SELECT * FROM newsletter_item WHERE source = ? ORDER BY published_at DESC, fetched_at DESC LIMIT 80", (source,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM newsletter_item ORDER BY published_at DESC, fetched_at DESC LIMIT 120").fetchall()
    return [{**dict(row), "read": bool(row["read"])} for row in rows]


@router.post("/fetch")
def fetch_now() -> dict:
    _ensure_table()
    fetched = 0
    errors = []
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        for source in _sources():
            name = str(source.get("name") or source.get("rss") or "Unknown")
            rss = source.get("rss")
            if not rss:
                continue
            parsed = feedparser.parse(rss)
            if parsed.bozo:
                errors.append({"source": name, "error": str(parsed.bozo_exception)[:300]})
            for entry in parsed.entries[:12]:
                title = entry.get("title", "Untitled")
                url = entry.get("link", "")
                item_id = _item_id(name, url, title)
                published = entry.get("published") or entry.get("updated") or now
                conn.execute(
                    """
                    INSERT OR IGNORE INTO newsletter_item (id, source, title, url, published_at, summary, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (item_id, name, title, url, published, _summary(entry), now),
                )
                if conn.total_changes:
                    fetched += 1
        conn.commit()
    return {"ok": True, "fetched": fetched, "errors": errors}


@router.post("/{item_id}/read")
def mark_read(item_id: str) -> dict:
    _ensure_table()
    with get_connection() as conn:
        cur = conn.execute("UPDATE newsletter_item SET read = 1 WHERE id = ?", (item_id,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Newsletter item not found")
    return {"ok": True}
