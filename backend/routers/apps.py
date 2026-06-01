from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse, RedirectResponse

from backend.database import PROJECT_DIR, get_connection, rows_to_dicts

router = APIRouter(prefix="/api/apps", tags=["apps"])
APPS_DIR = PROJECT_DIR / "apps"


class ComposeUpdate(BaseModel):
    content: str



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


def _compose_file(row: sqlite3.Row) -> Path:
    compose_path = row["compose_path"]
    if not compose_path:
        raise HTTPException(status_code=404, detail="Compose file not found")
    path = Path(compose_path).resolve()
    apps_root = APPS_DIR.resolve()
    if apps_root not in path.parents:
        raise HTTPException(status_code=400, detail="Compose path is outside the managed apps directory")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Compose file not found")
    return path


def _request_hostname(request: Request | None) -> str:
    """Return the hostname the browser used to reach the dashboard.

    App web UIs run on sibling ports of the dashboard host. Hard-coding
    lab.local breaks for users who open the dashboard by LAN IP, Tailscale IP,
    or another DNS name, so generate links from the incoming request host.
    """
    if request is None:
        return "lab.local"
    forwarded_host = request.headers.get("x-forwarded-host")
    host = (forwarded_host or request.headers.get("host") or request.url.hostname or "lab.local").split(",", 1)[0]
    hostname = host.rsplit(":", 1)[0] if host.count(":") <= 1 else request.url.hostname
    return hostname if hostname and hostname not in {"0.0.0.0", "::"} else "lab.local"


def _web_ui_path(path: str | None) -> str:
    if not path:
        return "/"
    return path if path.startswith("/") else f"/{path}"


def _web_ui_url(row: sqlite3.Row | dict, request: Request | None = None) -> str | None:
    app = dict(row)
    port = app.get("web_ui_port")
    if not port:
        return None
    return f"http://{_request_hostname(request)}:{port}{_web_ui_path(app.get('web_ui_path'))}"


def _serialize_app(row: sqlite3.Row, include_services: bool = False, request: Request | None = None) -> dict:
    app = dict(row)
    app_id = app["id"]
    app.update(
        {
            "icon_url": f"/api/apps/{quote(app_id)}/icon" if app.get("icon_path") else None,
            "status": _compose_status(app.get("compose_path")),
            "web_ui_url": _web_ui_url(row, request),
            "open_url": f"/api/apps/{quote(app_id)}/open" if app.get("web_ui_port") else None,
        }
    )
    if include_services:
        app["services"] = _read_service_names(app.get("compose_path"))
    return app


@router.get("")
def list_apps(request: Request) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM app WHERE enabled = 1 ORDER BY name COLLATE NOCASE").fetchall()
    return [_serialize_app(row, request=request) for row in rows]


@router.get("/{app_id}")
def get_app(app_id: str, request: Request) -> dict:
    return _serialize_app(_app_row(app_id), include_services=True, request=request)


@router.get("/{app_id}/open")
def open_app(app_id: str, request: Request) -> RedirectResponse:
    row = _app_row(app_id)
    target = _web_ui_url(row, request)
    if not target:
        raise HTTPException(status_code=404, detail="App has no web UI")
    return RedirectResponse(target, status_code=302)


@router.get("/{app_id}/icon")
def get_icon(app_id: str) -> FileResponse:
    icon = APPS_DIR / app_id / "icon.png"
    if not icon.exists():
        raise HTTPException(status_code=404, detail="Icon not found")
    return FileResponse(icon)


@router.get("/{app_id}/compose")
def get_compose(app_id: str) -> dict[str, str]:
    row = _app_row(app_id)
    path = _compose_file(row)
    return {"app_id": app_id, "path": str(path), "content": path.read_text(encoding="utf-8")}


@router.get("/{app_id}/logs")
def get_logs(app_id: str, tail: int = Query(default=160, ge=20, le=1000)) -> dict[str, str | bool | int]:
    row = _app_row(app_id)
    compose_path = row["compose_path"]
    if not compose_path or not Path(compose_path).exists():
        raise HTTPException(status_code=404, detail="Compose file not found")
    result = subprocess.run(
        ["docker", "compose", "-f", compose_path, "logs", "--no-color", f"--tail={tail}"],
        check=False,
        text=True,
        capture_output=True,
        timeout=20,
    )
    output = (result.stdout + result.stderr).strip()
    return {"ok": result.returncode == 0, "tail": tail, "output": output}


@router.put("/{app_id}/compose")
def update_compose(app_id: str, payload: ComposeUpdate) -> dict[str, str | bool]:
    row = _app_row(app_id)
    path = _compose_file(row)
    try:
        parsed = yaml.safe_load(payload.content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict) or not parsed["services"]:
        raise HTTPException(status_code=400, detail="Compose must contain a non-empty services mapping")
    path.write_text(payload.content.rstrip() + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path)}


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
