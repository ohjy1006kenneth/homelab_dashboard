from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request as UrlRequest, urlopen

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, RedirectResponse

from backend.database import DATA_DIR, PROJECT_DIR, get_connection

router = APIRouter(prefix="/api/apps", tags=["apps"])
APPS_DIR = PROJECT_DIR / "apps"
BACKUP_DIR = DATA_DIR / "compose_backups"


class ComposeUpdate(BaseModel):
    content: str


class AppCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    category: str | None = "Manual"
    web_ui_port: int | None = Field(default=None, ge=1, le=65535)
    web_ui_path: str = "/"
    compose: str = Field(min_length=1)


class ComposeRestore(BaseModel):
    backup_id: str


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _app_row(app_id: str, enabled_only: bool = True) -> sqlite3.Row:
    sql = "SELECT * FROM app WHERE id = ?"
    params: tuple[str, ...] = (app_id,)
    if enabled_only:
        sql += " AND enabled = 1"
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
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


def _read_compose(compose_path: str | None) -> dict:
    if not compose_path:
        return {}
    try:
        return yaml.safe_load(Path(compose_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


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


DEFAULT_HOSTNAME = "pi-homelab"


def _request_hostname(request: Request | None) -> str:
    if request is None:
        return DEFAULT_HOSTNAME
    forwarded_host = request.headers.get("x-forwarded-host")
    host = (forwarded_host or request.headers.get("host") or request.url.hostname or DEFAULT_HOSTNAME).split(",", 1)[0]
    hostname = host.rsplit(":", 1)[0] if host.count(":") <= 1 else request.url.hostname
    return hostname if hostname and hostname not in {"0.0.0.0", "::"} else DEFAULT_HOSTNAME


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


def _localhost_url(row: sqlite3.Row | dict) -> str | None:
    app = dict(row)
    port = app.get("web_ui_port")
    if not port:
        return None
    return f"http://127.0.0.1:{port}{_web_ui_path(app.get('web_ui_path'))}"


def _is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _web_health(row: sqlite3.Row | dict) -> dict[str, str | int | bool | None]:
    url = _localhost_url(row)
    port = dict(row).get("web_ui_port")
    if not url or not port:
        return {"ok": None, "status": "no-ui", "http_code": None, "error": None}
    if not _is_port_open(int(port)):
        return {"ok": False, "status": "port-closed", "http_code": None, "error": "port is not accepting TCP connections"}
    req = UrlRequest(url, headers={"User-Agent": "lab-dashboard-health/1.0"})
    try:
        with urlopen(req, timeout=2.0) as response:
            code = response.getcode()
        return {"ok": 200 <= code < 500, "status": "reachable", "http_code": code, "error": None}
    except Exception as exc:  # noqa: BLE001 - health report should not break API
        return {"ok": False, "status": "http-error", "http_code": None, "error": str(exc)[:180]}


def _compose_ports(compose: dict) -> list[int]:
    ports: set[int] = set()
    services = compose.get("services") or {}
    if not isinstance(services, dict):
        return []
    for service in services.values():
        if not isinstance(service, dict):
            continue
        for item in service.get("ports") or []:
            host_port = None
            if isinstance(item, str):
                left = item.split("/", 1)[0].split(":")
                host_port = left[-2] if len(left) >= 2 else left[0]
            elif isinstance(item, dict):
                host_port = item.get("published") or item.get("host_port") or item.get("target")
            try:
                if host_port is not None:
                    ports.add(int(str(host_port).strip('"')))
            except ValueError:
                pass
    return sorted(p for p in ports if 0 < p <= 65535)


def _port_report(port: int, current_app_id: str | None = None) -> dict:
    owner = None
    with get_connection() as conn:
        row = conn.execute("SELECT id, name FROM app WHERE enabled = 1 AND web_ui_port = ?", (port,)).fetchone()
        if row:
            owner = {"id": row["id"], "name": row["name"]}
    return {
        "port": port,
        "open": _is_port_open(port),
        "known_owner": owner,
        "conflict": bool(owner and owner["id"] != current_app_id),
    }


def _backup_compose(app_id: str, path: Path, reason: str) -> dict[str, str | int]:
    app_backup_dir = BACKUP_DIR / app_id
    app_backup_dir.mkdir(parents=True, exist_ok=True)
    backup_id = f"{_utc_stamp()}-{reason}.yml"
    destination = app_backup_dir / backup_id
    shutil.copy2(path, destination)
    return {"backup_id": backup_id, "path": str(destination), "bytes": destination.stat().st_size}


def _backup_list(app_id: str) -> list[dict[str, str | int]]:
    app_backup_dir = BACKUP_DIR / app_id
    if not app_backup_dir.exists():
        return []
    backups = []
    for path in sorted(app_backup_dir.glob("*.yml"), reverse=True):
        backups.append({"backup_id": path.name, "path": str(path), "bytes": path.stat().st_size, "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
    return backups


def _serialize_app(row: sqlite3.Row, include_services: bool = False, request: Request | None = None, include_health: bool = False) -> dict:
    app = dict(row)
    app_id = app["id"]
    status = _compose_status(app.get("compose_path"))
    app.update(
        {
            "icon_url": f"/api/apps/{quote(app_id)}/icon" if app.get("icon_path") else None,
            "status": status,
            "web_ui_url": _web_ui_url(row, request),
            "open_url": f"/api/apps/{quote(app_id)}/open" if app.get("web_ui_port") else None,
        }
    )
    if include_services:
        compose = _read_compose(app.get("compose_path"))
        app["services"] = _read_service_names(app.get("compose_path"))
        app_ports: list[int] = []
        if app.get("web_ui_port"):
            app_ports.append(int(app["web_ui_port"]))
        app_ports.extend(_compose_ports(compose))
        app["ports"] = [_port_report(port, app_id) for port in sorted(set(app_ports))]
        app["backups"] = _backup_list(app_id)
    if include_health:
        app["health"] = _web_health(row)
    return app


@router.get("")
def list_apps(request: Request, health: bool = False) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM app WHERE enabled = 1 ORDER BY name COLLATE NOCASE").fetchall()
    return [_serialize_app(row, request=request, include_health=health) for row in rows]


@router.post("")
def create_app(payload: AppCreate, request: Request) -> dict:
    app_id = payload.id.strip().lower()
    try:
        compose = yaml.safe_load(payload.compose)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict) or not compose["services"]:
        raise HTTPException(status_code=400, detail="Compose must contain a non-empty services mapping")
    app_dir = APPS_DIR / app_id
    if app_dir.exists():
        raise HTTPException(status_code=409, detail="An app directory with this id already exists")
    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM app WHERE id = ? AND enabled = 1", (app_id,)).fetchone():
            raise HTTPException(status_code=409, detail="An enabled app with this id already exists")
    ports = [payload.web_ui_port, *_compose_ports(compose)]
    conflicts = [_port_report(int(port), app_id) for port in sorted(set(p for p in ports if p))]
    if any(item["conflict"] for item in conflicts):
        raise HTTPException(status_code=409, detail={"message": "Port conflicts detected", "ports": conflicts})
    app_dir.mkdir(parents=True, exist_ok=False)
    compose_path = app_dir / "docker-compose.yml"
    meta_path = app_dir / "meta.json"
    web_path = _web_ui_path(payload.web_ui_path)
    compose_path.write_text(payload.compose.rstrip() + "\n", encoding="utf-8")
    meta = {
        "id": app_id,
        "name": payload.name,
        "description": payload.description,
        "category": payload.category,
        "icon": None,
        "web_ui_port": payload.web_ui_port,
        "web_ui_path": web_path,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "source": "manual",
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO app (id, name, description, category, icon_path, web_ui_port, web_ui_path, compose_path, enabled, added_at, source, appdata_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'manual', NULL)
            """,
            (app_id, payload.name, payload.description, payload.category, None, payload.web_ui_port, web_path, str(compose_path), meta["added_at"]),
        )
        conn.commit()
    return {"ok": True, "app": _serialize_app(_app_row(app_id), include_services=True, request=request), "ports": conflicts}


@router.get("/ports/check")
def check_ports(ports: str = Query(..., description="Comma-separated ports")) -> dict:
    parsed: list[int] = []
    for item in ports.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            port = int(item)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid port: {item}") from exc
        if port < 1 or port > 65535:
            raise HTTPException(status_code=400, detail=f"Invalid port: {port}")
        parsed.append(port)
    return {"ports": [_port_report(port) for port in sorted(set(parsed))]}


@router.get("/{app_id}")
def get_app(app_id: str, request: Request) -> dict:
    return _serialize_app(_app_row(app_id), include_services=True, request=request, include_health=True)


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


@router.get("/{app_id}/backups")
def get_backups(app_id: str) -> dict:
    _app_row(app_id)
    return {"app_id": app_id, "backups": _backup_list(app_id)}


@router.post("/{app_id}/compose/restore")
def restore_compose(app_id: str, payload: ComposeRestore) -> dict[str, str | bool]:
    row = _app_row(app_id)
    path = _compose_file(row)
    backup_path = (BACKUP_DIR / app_id / payload.backup_id).resolve()
    backup_root = (BACKUP_DIR / app_id).resolve()
    if backup_root not in backup_path.parents or not backup_path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    _backup_compose(app_id, path, "pre-restore")
    shutil.copy2(backup_path, path)
    return {"ok": True, "path": str(path), "restored_from": payload.backup_id}


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


@router.get("/{app_id}/health")
def get_health(app_id: str) -> dict:
    row = _app_row(app_id)
    return {"app_id": app_id, "status": _compose_status(row["compose_path"]), "health": _web_health(row)}


@router.put("/{app_id}/compose")
def update_compose(app_id: str, payload: ComposeUpdate) -> dict[str, str | bool | dict]:
    row = _app_row(app_id)
    path = _compose_file(row)
    try:
        parsed = yaml.safe_load(payload.content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict) or not parsed["services"]:
        raise HTTPException(status_code=400, detail="Compose must contain a non-empty services mapping")
    backup = _backup_compose(app_id, path, "pre-edit")
    path.write_text(payload.content.rstrip() + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path), "backup": backup}


def _container_ids(compose_path: str) -> list[str]:
    result = subprocess.run(
        ["docker", "compose", "-f", compose_path, "ps", "-q"],
        check=False,
        text=True,
        capture_output=True,
        timeout=8,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _run_compose(app_id: str, args: list[str]) -> dict[str, str | bool | list[dict]]:
    row = _app_row(app_id)
    compose_path = row["compose_path"]
    if not compose_path or not Path(compose_path).exists():
        raise HTTPException(status_code=404, detail="Compose file not found")
    if args and args[0] == "up":
        compose = _read_compose(compose_path)
        action_ports: list[int] = []
        if row["web_ui_port"]:
            action_ports.append(int(row["web_ui_port"]))
        action_ports.extend(_compose_ports(compose))
        conflicts = [_port_report(port, app_id) for port in sorted(set(action_ports))]
        hard_conflicts = [item for item in conflicts if item["conflict"]]
        if hard_conflicts:
            raise HTTPException(status_code=409, detail={"message": "Port conflicts detected", "ports": hard_conflicts})
    result = subprocess.run(
        ["docker", "compose", "-f", compose_path, *args],
        check=False,
        text=True,
        capture_output=True,
        timeout=120,
    )
    return {"ok": result.returncode == 0, "output": (result.stdout + result.stderr).strip()}


@router.get("/{app_id}/stats")
def get_stats(app_id: str) -> dict:
    row = _app_row(app_id)
    compose_path = row["compose_path"]
    if not compose_path or not Path(compose_path).exists():
        raise HTTPException(status_code=404, detail="Compose file not found")
    ids = _container_ids(compose_path)
    if not ids:
        return {"app_id": app_id, "containers": []}
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
        check=False,
        text=True,
        capture_output=True,
        timeout=12,
    )
    containers = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append(
            {
                "name": item.get("Name"),
                "container": item.get("Container"),
                "cpu": item.get("CPUPerc"),
                "memory": item.get("MemUsage"),
                "memory_pct": item.get("MemPerc"),
                "network": item.get("NetIO"),
                "block_io": item.get("BlockIO"),
                "pids": item.get("PIDs"),
            }
        )
    return {"app_id": app_id, "ok": result.returncode == 0, "containers": containers, "error": result.stderr.strip()}


@router.post("/{app_id}/start")
def start_app(app_id: str) -> dict:
    return _run_compose(app_id, ["up", "-d"])


@router.post("/{app_id}/stop")
def stop_app(app_id: str) -> dict:
    return _run_compose(app_id, ["down"])


@router.post("/{app_id}/restart")
def restart_app(app_id: str) -> dict:
    return _run_compose(app_id, ["restart"])


@router.delete("/{app_id}")
def archive_app(app_id: str) -> dict[str, str | bool]:
    row = _app_row(app_id)
    path = _compose_file(row)
    _backup_compose(app_id, path, "pre-archive")
    archive_dir = path.parent / "archived"
    archive_dir.mkdir(exist_ok=True)
    archived_path = archive_dir / f"docker-compose.{_utc_stamp()}.yml"
    shutil.move(str(path), archived_path)
    with get_connection() as conn:
        conn.execute("UPDATE app SET enabled = 0 WHERE id = ?", (app_id,))
        conn.commit()
    return {"ok": True, "archived_path": str(archived_path)}
