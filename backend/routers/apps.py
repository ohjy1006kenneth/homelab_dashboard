from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.database import PROJECT_DIR, get_connection, rows_to_dicts

router = APIRouter(prefix="/api/apps", tags=["apps"])
APPS_DIR = PROJECT_DIR / "apps"


def _app_row(app_id: str) -> sqlite3.Row:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM app WHERE id = ? AND enabled = 1", (app_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="App not found")
    return row


def _read_service_names(compose_path: str | None) -> list[str]:
    if not compose_path:
        return []
    path = Path(compose_path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    services = data.get("services") or {}
    return list(services.keys()) if isinstance(services, dict) else []


def _compose_status(compose_path: str | None) -> str:
    if not compose_path or not Path(compose_path).exists():
        return "missing"
    result = subprocess.run(
        ["docker", "compose", "-f", compose_path, "ps", "--format", "json"],
        check=False,
        text=True,
        capture_output=True,
        timeout=8,
    )
    if result.returncode != 0:
        # Docker may be unavailable to the current user; keep the dashboard usable.
        return "unknown"
    output = result.stdout.strip()
    if not output:
        return "stopped"
    running = any('"State":"running"' in line or '"Status":"running"' in line for line in output.splitlines())
    return "running" if running else "stopped"


def _serialize_app(row: sqlite3.Row, include_services: bool = False) -> dict:
    app = dict(row)
    app_id = app["id"]
    port = app.get("web_ui_port")
    web_path = app.get("web_ui_path") or "/"
    app.update(
        {
            "icon_url": f"/api/apps/{quote(app_id)}/icon" if app.get("icon_path") else None,
            "status": _compose_status(app.get("compose_path")),
            "web_ui_url": f"http://lab.local:{port}{web_path}" if port else None,
        }
    )
    if include_services:
        app["services"] = _read_service_names(app.get("compose_path"))
    return app


@router.get("")
def list_apps() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM app WHERE enabled = 1 ORDER BY name COLLATE NOCASE").fetchall()
    return [_serialize_app(row) for row in rows]


@router.get("/{app_id}")
def get_app(app_id: str) -> dict:
    return _serialize_app(_app_row(app_id), include_services=True)


@router.get("/{app_id}/icon")
def get_icon(app_id: str) -> FileResponse:
    icon = APPS_DIR / app_id / "icon.png"
    if not icon.exists():
        raise HTTPException(status_code=404, detail="Icon not found")
    return FileResponse(icon)


def _run_compose(app_id: str, args: list[str]) -> dict[str, str | bool]:
    row = _app_row(app_id)
    compose_path = row["compose_path"]
    if not compose_path or not Path(compose_path).exists():
        raise HTTPException(status_code=404, detail="Compose file not found")
    result = subprocess.run(
        ["docker", "compose", "-f", compose_path, *args],
        check=False,
        text=True,
        capture_output=True,
        timeout=120,
    )
    return {"ok": result.returncode == 0, "output": (result.stdout + result.stderr).strip()}


@router.post("/{app_id}/start")
def start_app(app_id: str) -> dict[str, str | bool]:
    return _run_compose(app_id, ["up", "-d"])


@router.post("/{app_id}/stop")
def stop_app(app_id: str) -> dict[str, str | bool]:
    return _run_compose(app_id, ["down"])


@router.post("/{app_id}/restart")
def restart_app(app_id: str) -> dict[str, str | bool]:
    return _run_compose(app_id, ["restart"])
