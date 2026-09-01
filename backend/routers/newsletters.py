from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from backend.database import PROJECT_DIR, get_connection
from cron.run_news_cycle import run_cycle

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


@router.get("/sources")
def sources() -> list[dict]:
    return _sources()


@router.get("/curation")
def curation() -> dict:
    """Return the last persisted brief using a stable empty contract."""
    _ensure_table()
    with get_connection() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS newsletter_brief (
                id INTEGER PRIMARY KEY CHECK (id = 1), generated_at TEXT NOT NULL,
                source TEXT NOT NULL, headline TEXT NOT NULL, items_json TEXT NOT NULL
            )"""
        )
        row = conn.execute("SELECT generated_at, source, headline, items_json FROM newsletter_brief WHERE id = 1").fetchone()
    if not row:
        return {"generated_at": None, "source": "", "headline": "", "items": []}
    try:
        items = json.loads(row["items_json"])
    except (TypeError, json.JSONDecodeError):
        items = []
    return {"generated_at": row["generated_at"], "source": row["source"], "headline": row["headline"], "items": items if isinstance(items, list) else []}


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
    try:
        report = run_cycle()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="News cycle failed") from exc
    return {"ok": True, "fetched": report["fetched_new"], "errors": report["errors"], **report}


@router.post("/{item_id}/read")
def mark_read(item_id: str) -> dict:
    _ensure_table()
    with get_connection() as conn:
        cur = conn.execute("UPDATE newsletter_item SET read = 1 WHERE id = ?", (item_id,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Newsletter item not found")
    return {"ok": True}
