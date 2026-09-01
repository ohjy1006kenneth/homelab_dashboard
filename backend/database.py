from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Any, Iterable, cast

import yaml

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = DATA_DIR / "dashboard.db"
APPS_DIR = PROJECT_DIR / "apps"

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS app (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT,
    icon_path TEXT,
    web_ui_port INTEGER,
    web_ui_path TEXT NOT NULL DEFAULT '/',
    compose_path TEXT NOT NULL,
    appdata_path TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'managed'
)
"""


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]


def _compose_path(app_dir: Path) -> Path | None:
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        candidate = app_dir / name
        if candidate.is_file():
            return candidate
    return None


def _host_port(compose: dict) -> int | None:
    services = compose.get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict):
        return None
    for service in services.values():
        if not isinstance(service, dict):
            continue
        ports = service.get("ports") or []
        if not isinstance(ports, list):
            continue
        for entry in ports:
            value = entry.get("published") if isinstance(entry, dict) else entry
            if isinstance(value, str):
                value = value.split("/", 1)[0].split(":")[-2 if ":" in value else -1]
            try:
                if value is None:
                    continue
                port = int(str(value))
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535 and port not in (53, 67):
                return port
    return None


def ensure_app_catalog() -> dict[str, object]:
    """Create the app schema and reconcile safe, project-local manifests.

    This intentionally reads existing project files only. It never copies appdata,
    rewrites compose files, or changes an existing row's enabled state.
    """
    report: dict[str, Any] = {"created": 0, "updated": 0, "skipped": []}
    with get_connection() as conn:
        conn.execute(APP_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(app)")}
        # The catalog predates the current schema in some installations.  Keep
        # this additive and idempotent: ALTER TABLE can safely add each missing
        # column when supplied with a default for required fields.
        migrations = {
            "description": "ALTER TABLE app ADD COLUMN description TEXT",
            "category": "ALTER TABLE app ADD COLUMN category TEXT",
            "icon_path": "ALTER TABLE app ADD COLUMN icon_path TEXT",
            "web_ui_port": "ALTER TABLE app ADD COLUMN web_ui_port INTEGER",
            "web_ui_path": "ALTER TABLE app ADD COLUMN web_ui_path TEXT NOT NULL DEFAULT '/'",
            "compose_path": "ALTER TABLE app ADD COLUMN compose_path TEXT NOT NULL DEFAULT ''",
            "appdata_path": "ALTER TABLE app ADD COLUMN appdata_path TEXT",
            "enabled": "ALTER TABLE app ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
            "added_at": "ALTER TABLE app ADD COLUMN added_at TEXT NOT NULL DEFAULT ''",
            "source": "ALTER TABLE app ADD COLUMN source TEXT NOT NULL DEFAULT 'managed'",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)
                columns.add(column)
        if not APPS_DIR.is_dir():
            conn.commit()
            return report
        for app_dir in sorted(APPS_DIR.iterdir()):
            if not app_dir.is_dir():
                continue
            meta_path = app_dir / "meta.json"
            compose_path = _compose_path(app_dir)
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta, dict):
                    raise ValueError("manifest is not an object")
                app_id = str(meta.get("id") or app_dir.name).strip()
                name = str(meta.get("name") or "").strip()
                if not app_id or not name:
                    raise ValueError("missing id or name")
                compose = {}
                if compose_path is not None:
                    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
                    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict) or not compose["services"]:
                        raise ValueError("compose has no services mapping")
                else:
                    # Keep a valid manifest visible with an explicit missing state.
                    compose_path = app_dir / "docker-compose.yml"
                now = str(meta.get("added_at") or "") or "managed"
                icon = app_dir / "icon.png"
                values = (
                    app_id, name, meta.get("description"), meta.get("category"),
                    str(icon) if icon.is_file() else None,
                    meta.get("web_ui_port") or _host_port(compose),
                    str(meta.get("web_ui_path") or "/"), str(compose_path),
                    meta.get("appdata_path"), now,
                    str(meta.get("source") or "managed"),
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError) as exc:
                cast(list[dict[str, str]], report["skipped"]).append({"id": app_dir.name, "reason": str(exc)})
                continue
            existing = conn.execute("SELECT enabled, added_at, appdata_path FROM app WHERE id = ?", (app_id,)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE app SET name=?, description=?, category=?, icon_path=?, web_ui_port=?,
                    web_ui_path=?, compose_path=?, appdata_path=COALESCE(?, appdata_path), source=? WHERE id=?""",
                    (values[1], values[2], values[3], values[4], values[5], values[6], values[7], values[8], values[10], app_id),
                )
                report["updated"] += 1
            else:
                conn.execute(
                    """INSERT INTO app (id,name,description,category,icon_path,web_ui_port,web_ui_path,
                    compose_path,appdata_path,enabled,added_at,source) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)""",
                    values,
                )
                report["created"] += 1
        conn.commit()
    return report
