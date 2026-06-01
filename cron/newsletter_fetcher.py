#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import feedparser

PROJECT_DIR = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_DIR / "dashboard.config.json"
OUT = PROJECT_DIR / "data" / "newsletter_fetcher_last.json"


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "sources": []}
    for source in cfg.get("newsletter_sources", []):
        parsed = feedparser.parse(source.get("rss"))
        report["sources"].append({"name": source.get("name"), "count": len(parsed.entries), "error": str(parsed.bozo_exception) if parsed.bozo else None})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Fetched metadata for {len(report['sources'])} newsletter sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
