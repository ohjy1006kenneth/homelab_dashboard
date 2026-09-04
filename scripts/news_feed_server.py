#!/usr/bin/env python3
"""Homarr companion server: reference-style overview plus news-brief data."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/home/juyoungoh/dashcraft")
DB_PATH = ROOT / "data/dashboard.db"
OVERVIEW_PATH = ROOT / "frontend/homarr-overview.html"
ASSET_DIR = ROOT / "assets/homarr"
PORT = int(os.environ.get("NEWS_FEED_PORT", "7878"))
MAX_ITEMS = 20

SERVICES = {
    "portainer": ("Portainer", "10.1.188.19", 9000),
    "immich": ("Immich", "10.1.188.19", 2283),
    "syncthing": ("Syncthing", "10.1.188.19", 8384),
    "pihole": ("Pi-hole", "10.1.188.19", 8080),
    "jellyfin": ("Jellyfin", "10.1.188.19", 8096),
    "aiostreams": ("AI Streams", "10.1.188.19", 3000),
}


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def human_size(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit in {"B", "KB", "MB"} else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def read_memory() -> tuple[int, str]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        total = values["MemTotal"]
        available = values.get("MemAvailable", 0)
        used = total - available
        return round(used / total * 100), human_size(used)
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return 0, "—"


def read_temperature() -> tuple[int, str]:
    try:
        celsius = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        percent = max(0, min(100, round(celsius / 85 * 100)))
        return percent, f"{celsius:.0f}°C"
    except (OSError, ValueError):
        return 0, "—"


def service_statuses() -> tuple[dict[str, bool], list[dict[str, object]]]:
    statuses: dict[str, bool] = {}
    rows: list[dict[str, object]] = []
    for key, (name, host, port) in SERVICES.items():
        try:
            with socket.create_connection((host, port), timeout=0.35):
                up = True
        except OSError:
            up = False
        statuses[key] = up
        rows.append({"name": name, "up": up, "endpoint": f":{port}"})
    return statuses, rows


def drive_data() -> list[dict[str, object]]:
    candidates = [("System", Path("/")), ("Storage", Path("/srv/storage"))]
    result: list[dict[str, object]] = []
    seen: set[int] = set()
    for name, path in candidates:
        if not path.exists():
            continue
        try:
            device = path.stat().st_dev
            if device in seen:
                continue
            seen.add(device)
            total, used, free = shutil.disk_usage(path)
            result.append({
                "name": name,
                "free": human_size(free),
                "percent": round(used / total * 100) if total else 0,
            })
        except OSError:
            continue
    return result


def overview_payload() -> dict[str, object]:
    statuses, rows = service_statuses()
    memory_percent, memory_value = read_memory()
    temperature_percent, temperature_value = read_temperature()
    cpu_count = os.cpu_count() or 1
    try:
        load_percent = max(0, min(100, round(os.getloadavg()[0] / cpu_count * 100)))
        load_value = f"{os.getloadavg()[0]:.2f}"
    except OSError:
        load_percent, load_value = 0, "—"
    return {
        "services": statuses,
        "service_rows": rows,
        "metrics": [
            {"label": "load", "percent": load_percent, "value": load_value},
            {"label": "memory", "percent": memory_percent, "value": memory_value},
            {"label": "temperature", "percent": temperature_percent, "value": temperature_value},
        ],
        "drives": drive_data(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_news() -> list[dict[str, str]]:
    if not DB_PATH.exists():
        return []
    connection = sqlite3.connect(str(DB_PATH))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT source, title, url, published_at, summary "
            "FROM newsletter_item ORDER BY published_at DESC, fetched_at DESC LIMIT ?",
            (MAX_ITEMS,),
        ).fetchall()
    finally:
        connection.close()
    items: list[dict[str, str]] = []
    excluded_title_terms = ("roundup", "digest", "review:", "weekly review", "daily review")
    for row in rows:
        item = dict(row)
        title = item.get("title") or "Untitled"
        if any(term in title.lower() for term in excluded_title_terms):
            continue
        published = item.get("published_at") or ""
        try:
            published = datetime.fromisoformat(published.replace("Z", "+00:00")).strftime("%b %d")
        except (ValueError, TypeError):
            published = published[:10]
        items.append({
            "source": item.get("source") or "",
            "title": title,
            "url": item.get("url") or "#",
            "published": published,
            "summary": item.get("summary") or "",
        })
        if len(items) == 5:
            break
    return items


class DashboardHandler(BaseHTTPRequestHandler):
    def send_content(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route in {"/", "/overview"}:
            self.send_content(OVERVIEW_PATH.read_bytes(), "text/html; charset=utf-8")
        elif route in {"/api/news", "/news"}:
            self.send_content(json_bytes({"items": fetch_news()}), "application/json; charset=utf-8")
        elif route == "/api/overview":
            self.send_content(json_bytes(overview_payload()), "application/json; charset=utf-8")
        elif route == "/api/health":
            self.send_content(json_bytes({"ok": True}), "application/json")
        elif route.startswith("/assets/"):
            filename = Path(route).name
            asset = ASSET_DIR / filename
            if asset.exists() and asset.is_file():
                kind = "image/jpeg" if asset.suffix.lower() in {".jpg", ".jpeg"} else "application/octet-stream"
                self.send_content(asset.read_bytes(), kind)
            else:
                self.send_content(b"Not found", "text/plain", 404)
        else:
            self.send_content(b"Not found", "text/plain", 404)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"Homarr companion server running on http://0.0.0.0:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
