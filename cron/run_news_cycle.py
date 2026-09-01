#!/usr/bin/env python3
"""Canonical RSS fetch, summarization, and daily curation cycle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import feedparser

PROJECT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_DIR / "dashboard.config.json"
DB_PATH = PROJECT_DIR / "data" / "dashboard.db"
CATEGORIES = ("Semiconductor", "Stocks", "AI")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def item_id(source: str, url: str, title: str) -> str:
    return hashlib.sha1(f"{source}|{url}|{title}".encode()).hexdigest()[:20]


def clean_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(text.replace("\n", " ").split())


def paragraphs(text: str, count: int) -> str:
    text = clean_text(text)
    if not text:
        text = "No summary is available for this article."
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if count == 1:
        return text[:700].rstrip() + ("…" if len(text) > 700 else "")
    midpoint = max(1, len(sentences) // 2)
    first, second = " ".join(sentences[:midpoint]).strip(), " ".join(sentences[midpoint:]).strip()
    if not second:
        words = text.split()
        midpoint = max(1, len(words) // 2)
        first, second = " ".join(words[:midpoint]).strip(), " ".join(words[midpoint:]).strip()
    if not first:
        first = text
    if not second:
        second = first
    return f"{first.strip()}\n\n{second.strip()}"


def configured() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def ensure_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS newsletter_item (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
        published_at TEXT, summary TEXT, read INTEGER NOT NULL DEFAULT 0, fetched_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS newsletter_brief (
        id INTEGER PRIMARY KEY CHECK (id = 1), generated_at TEXT NOT NULL, source TEXT NOT NULL,
        headline TEXT NOT NULL, items_json TEXT NOT NULL)""")


def hermes_generate(items: list[dict], config: dict) -> dict:
    """Ask the configured Hermes profile for validated JSON; raise on any failure."""
    prompt = {
        "task": "Return only JSON with keys summaries and items.",
        "summaries": "For each input id, provide one concise paragraph summary.",
        "items": "Choose at most one eligible story per category in order Semiconductor, Stocks, AI; each summary must be exactly two paragraphs. Exclude roundup/digest.",
        "input": items,
    }
    cmd = os.environ.get("DASHCRAFT_HERMES_COMMAND", "hermes")
    profile = str(config.get("news_summarizer", {}).get("profile", "news-brief"))
    argv = cmd.split() + ["--profile", profile, "chat", "-q", json.dumps(prompt), "-Q"]
    result = subprocess.run(argv, cwd=PROJECT_DIR, text=True, capture_output=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f"Hermes exited {result.returncode}: {result.stderr[-300:]}")
    raw = result.stdout.strip()
    if not raw:
        raise ValueError("Hermes did not return JSON")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("summaries"), dict) or not isinstance(data.get("items"), list):
        raise ValueError("Hermes JSON schema invalid")
    return data


def is_roundup(item: dict) -> bool:
    return bool(re.search(r"\b(roundup|round-up|digest|weekly\s+review|in\s+review)\b", f"{item.get('title','')} {item.get('summary','')}", re.I))


def fallback(items: list[dict], categories: list[str]) -> tuple[dict, list[dict]]:
    summaries = {str(i["id"]): paragraphs(i.get("raw_summary") or i.get("title"), 1) for i in items}
    chosen = []
    for category in categories:
        candidates = [i for i in items if str(i.get("category", "")).lower() == category.lower() and not is_roundup(i)]
        if candidates:
            story = dict(candidates[0])
            story["summary"] = paragraphs(story.get("raw_summary") or story.get("title"), 2)
            chosen.append({
                "category": category,
                **{k: story.get(k, "") for k in ("title", "url", "source", "published_at", "summary")},
            })
    return summaries, chosen[:3]


def validate_generated(data: dict, items: list[dict], categories: list[str]) -> tuple[dict[str, str], list[dict]]:
    """Validate and canonicalize the complete agent result before persistence."""
    by_id = {str(item["id"]): item for item in items}
    by_url = {str(item["url"]): item for item in items if item.get("url")}
    summaries = data.get("summaries")
    curated = data.get("items")
    if not isinstance(summaries, dict) or not isinstance(curated, list):
        raise ValueError("Hermes JSON schema invalid")
    if (any(not isinstance(item_id, str) or not isinstance(value, str) for item_id, value in summaries.items())
            or set(summaries) != set(by_id)
            or any(not clean_text(value) for value in summaries.values())):
        raise ValueError("Hermes summaries ids or values invalid")
    normalized_summaries = {item_id: paragraphs(summaries[item_id], 1) for item_id in by_id}
    allowed = {str(category).lower(): str(category) for category in categories}
    seen: set[str] = set()
    seen_grounding: set[str] = set()
    normalized: list[dict] = []
    for candidate in curated:
        if not isinstance(candidate, dict):
            raise ValueError("Hermes curation item invalid")
        category = candidate.get("category")
        if not isinstance(category, str):
            raise ValueError("Hermes curation category invalid")
        category_key = category.lower()
        if category_key not in allowed or category_key in seen:
            raise ValueError("Hermes curation categories invalid")
        grounding = next((candidate[field] for field in ("id", "source_id", "url") if field in candidate), None)
        if not isinstance(grounding, str):
            raise ValueError("Hermes curation grounding selector invalid")
        source_item = by_id.get(grounding)
        if source_item is None:
            source_item = by_url.get(grounding)
        summary_value = candidate.get("summary")
        if (source_item is None or is_roundup(source_item)
                or not isinstance(summary_value, str) or not clean_text(summary_value)):
            raise ValueError("Hermes curation grounding invalid")
        actual_category = source_item.get("category")
        if not isinstance(actual_category, str) or actual_category.lower() != category_key:
            raise ValueError("Hermes curation category does not match grounded source")
        source_key = str(source_item["id"])
        if source_key in seen_grounding or (source_item.get("url") and str(source_item["url"]) in seen_grounding):
            raise ValueError("Hermes curation grounding reused")
        summary = paragraphs(summary_value, 2)
        normalized.append({
            "category": allowed[category_key],
            "title": source_item["title"],
            "url": source_item["url"],
            "source": source_item["source"],
            "published_at": source_item.get("published_at"),
            "summary": summary,
        })
        seen.add(category_key)
        seen_grounding.add(source_key)
        if source_item.get("url"):
            seen_grounding.add(str(source_item["url"]))
    return normalized_summaries, [next(item for item in normalized if item["category"].lower() == category.lower()) for category in categories if any(item["category"].lower() == category.lower() for item in normalized)]


def run_cycle(*, dry_run: bool = False, feed_parser: Callable | None = None) -> dict:
    parser = feed_parser or feedparser.parse
    cfg = configured()
    retention = int(cfg.get("news_retention", {}).get("max_items", 300))
    display = int(cfg.get("news_retention", {}).get("display_limit", 10))
    categories = cfg.get("preferences", {}).get("news", {}).get("curated_categories", list(CATEGORIES))
    report = {"timestamp": now_iso(), "fetched_new": 0, "summarized": 0, "retention_limit": retention, "display_limit": display, "brief_items": 0, "errors": [], "dry_run": dry_run}
    planned = []
    fetched_at = report["timestamp"]
    for source in cfg.get("newsletter_sources", []):
        name, rss = str(source.get("name") or source.get("rss") or "Unknown"), source.get("rss")
        if not rss:
            report["errors"].append({"source": name, "error": "missing RSS URL"})
            continue
        try:
            parsed = parser(rss)
            if getattr(parsed, "bozo", False):
                report["errors"].append({"source": name, "error": str(parsed.bozo_exception)[:300]})
            for entry in getattr(parsed, "entries", [])[:12]:
                title, url = clean_text(entry.get("title") or "Untitled"), str(entry.get("link") or "")
                planned.append({"id": item_id(name, url, title), "source": name, "title": title, "url": url,
                                "published_at": entry.get("published") or entry.get("updated") or fetched_at,
                                "fetched_at": fetched_at, "raw_summary": clean_text(entry.get("summary") or entry.get("description")),
                                "category": source.get("category") or ("AI" if re.search(r"\bAI\b|artificial intelligence", f"{name} {title}", re.I) else "Semiconductor")})
        except Exception as exc:
            report["errors"].append({"source": name, "error": str(exc)[:300]})
    if dry_run:
        report["planned_items"] = len(planned)
        try:
            generated, curated = fallback(planned, categories)
            report["summarized"], report["brief_items"] = len(generated), len(curated)
        except Exception as exc:
            report["errors"].append({"stage": "planning", "error": str(exc)})
        return report

    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_table(conn)
        for i in planned:
            cur = conn.execute("INSERT OR IGNORE INTO newsletter_item (id,source,title,url,published_at,summary,fetched_at) VALUES (?,?,?,?,?,?,?)",
                               (i["id"], i["source"], i["title"], i["url"], i["published_at"], paragraphs(i["raw_summary"] or i["title"], 1), i["fetched_at"]))
            report["fetched_new"] += cur.rowcount
        rows = [dict(r) for r in conn.execute("SELECT * FROM newsletter_item ORDER BY fetched_at DESC, published_at DESC")]
        if len(rows) > retention:
            conn.executemany("DELETE FROM newsletter_item WHERE id = ?", [(r["id"],) for r in rows[retention:]])
        try:
            generated_raw = hermes_generate(planned, cfg)
            generated, curated = validate_generated(generated_raw, planned, categories)
            for i in planned:
                conn.execute("UPDATE newsletter_item SET summary = ? WHERE id = ?", (generated[str(i["id"])], i["id"]))
        except Exception as exc:
            report["errors"].append({"stage": "hermes", "error": str(exc)[:300]})
            generated, curated = fallback(planned, categories)
        report["summarized"], report["brief_items"] = len(generated), len(curated)
        brief = {"generated_at": now_iso(), "source": "news-brief", "headline": "Daily News Brief", "items": curated}
        conn.execute("INSERT OR REPLACE INTO newsletter_brief (id,generated_at,source,headline,items_json) VALUES (1,?,?,?,?)",
                     (brief["generated_at"], brief["source"], brief["headline"], json.dumps(curated)))
        conn.commit()
    finally:
        conn.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run_cycle(dry_run=args.dry_run), separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"timestamp": now_iso(), "fetched_new": 0, "summarized": 0, "retention_limit": 0, "display_limit": 0, "brief_items": 0, "errors": [{"stage": "cycle", "error": str(exc)}], "dry_run": args.dry_run}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
