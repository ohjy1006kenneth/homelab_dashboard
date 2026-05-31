#!/usr/bin/env python3
"""Migrate CasaOS app definitions into the lab.local dashboard.

This script copies each CasaOS app compose file into this project, extracts the
CasaOS metadata into apps/{app_id}/meta.json, downloads/copies icons when
available, copies matching /DATA/AppData/{app_id} directories into local
appdata/{app_id}, and upserts App rows into data/dashboard.db.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

CASAOS_APPS_DIR = Path("/var/lib/casaos/apps")
CASAOS_APPDATA_DIR = Path("/DATA/AppData")
PROJECT_DIR = Path("/home/juyoungoh/nas/Projects/dashboard")
DASHBOARD_APPS_DIR = PROJECT_DIR / "apps"
DASHBOARD_APPDATA_DIR = PROJECT_DIR / "appdata"
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = DATA_DIR / "dashboard.db"
REPORT_PATH = DATA_DIR / "migration_report.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def localized(value: Any) -> str | None:
    """Return a friendly string from CasaOS localized fields."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("custom", "en_us", "en", "default"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return str(value).strip() or None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return None


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a YAML mapping")
    return data


def get_resolved_compose(app_id: str) -> str | None:
    """Ask casaos-cli for resolved compose YAML, if that command exists/works."""
    cmd = ["casaos-cli", "app-management", "show", "local", app_id, "--yaml"]
    try:
        result = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    output = result.stdout.strip()
    if result.returncode == 0 and output:
        return output + "\n"
    return None


def find_compose_path(app_dir: Path) -> Path | None:
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        candidate = app_dir / name
        if candidate.exists():
            return candidate
    return None


def choose_main_service(compose: dict[str, Any], casaos: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    services = compose.get("services") or {}
    if not isinstance(services, dict) or not services:
        return None, {}

    main_name = casaos.get("main") or casaos.get("main_service")
    if main_name in services and isinstance(services[main_name], dict):
        return str(main_name), services[main_name]

    # Prefer the first service carrying x-casaos metadata, otherwise the first service.
    for name, service in services.items():
        if isinstance(service, dict) and "x-casaos" in service:
            return str(name), service
    name, service = next(iter(services.items()))
    return str(name), service if isinstance(service, dict) else {}


def extract_host_ports(service: dict[str, Any]) -> list[int]:
    host_ports: list[int] = []
    ports = service.get("ports") or []
    if not isinstance(ports, list):
        return host_ports

    for entry in ports:
        if isinstance(entry, dict):
            published = entry.get("published") or entry.get("host_port")
            port = safe_int(published)
            if port is not None:
                host_ports.append(port)
        elif isinstance(entry, str):
            # Handles "8080:80", "127.0.0.1:8080:80", and "80".
            no_proto = entry.split("/")[0]
            parts = no_proto.split(":")
            if len(parts) >= 2:
                port = safe_int(parts[-2])
            else:
                port = safe_int(parts[0])
            if port is not None:
                host_ports.append(port)
    return host_ports


def determine_web_port(casaos: dict[str, Any], service: dict[str, Any]) -> int | None:
    for key in ("port_map", "port", "web_ui_port"):
        port = safe_int(casaos.get(key))
        if port is not None:
            return port

    host_ports = extract_host_ports(service)
    if not host_ports:
        return None

    # Prefer common HTTP(S) app ports; otherwise first published host port.
    preferred = [p for p in host_ports if p not in (53, 67) and (p >= 80 or p in (443,))]
    return preferred[0] if preferred else host_ports[0]


def resolve_icon(app_id: str, app_dir: Path, app_out_dir: Path, casaos: dict[str, Any], service: dict[str, Any]) -> bool:
    local_icon = app_dir / "icon.png"
    output_icon = app_out_dir / "icon.png"
    if local_icon.exists():
        shutil.copy2(local_icon, output_icon)
        return True

    icon = casaos.get("icon") or casaos.get("thumbnail")
    labels = service.get("labels") if isinstance(service, dict) else None
    if not icon and isinstance(labels, dict):
        icon = labels.get("icon")
    if not icon and isinstance(labels, list):
        for label in labels:
            if isinstance(label, str) and label.startswith("icon="):
                icon = label.split("=", 1)[1]
                break

    if isinstance(icon, str) and icon.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(icon, headers={"User-Agent": "lab.local migration"})
            with urllib.request.urlopen(req, timeout=20) as response:
                output_icon.write_bytes(response.read())
            return True
        except Exception as exc:  # noqa: BLE001 - record and continue migration.
            print(f"Warning: failed to download icon for {app_id}: {exc}", file=sys.stderr)
            return False

    if isinstance(icon, str) and icon:
        candidate = (app_dir / icon).resolve() if not icon.startswith("/") else Path(icon)
        if candidate.exists() and candidate.is_file():
            shutil.copy2(candidate, output_icon)
            return True

    return False


def directory_size(path: Path) -> int:
    """Return directory size in bytes, ignoring unreadable files."""
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _err: None):
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except OSError:
                continue
    return total


def copy_appdata(app_id: str) -> dict[str, Any]:
    """Copy /DATA/AppData/{app_id} into project-local appdata/{app_id}."""
    appdata_in = CASAOS_APPDATA_DIR / app_id
    appdata_out = DASHBOARD_APPDATA_DIR / app_id

    if not appdata_in.exists():
        return {"source": str(appdata_in), "path": None, "status": "missing", "copied": False, "reason": "not found"}
    if not appdata_in.is_dir():
        return {"source": str(appdata_in), "path": None, "status": "skipped", "copied": False, "reason": "not a directory"}

    DASHBOARD_APPDATA_DIR.mkdir(parents=True, exist_ok=True)

    # Prefer rsync when present: it is resumable, preserves metadata, and keeps
    # repeated migrations fast. Fall back to shutil on minimal installs.
    rsync = shutil.which("rsync")
    if rsync:
        result = subprocess.run(
            [rsync, "-a", "--delete", f"{appdata_in}/", f"{appdata_out}/"],
            check=False,
            text=True,
            capture_output=True,
            timeout=900,
        )
        if result.returncode != 0:
            reason = (result.stderr or result.stdout or f"rsync exited {result.returncode}").strip()
            partial = appdata_out.exists()
            return {
                "source": str(appdata_in),
                "path": str(appdata_out) if partial else None,
                "status": "partial" if partial else "failed",
                "copied": False,
                "reason": reason,
                "size_bytes": directory_size(appdata_out) if partial else None,
            }
    else:
        shutil.copytree(appdata_in, appdata_out, dirs_exist_ok=True, symlinks=True)

    return {
        "source": str(appdata_in),
        "path": str(appdata_out),
        "status": "copied",
        "copied": True,
        "size_bytes": directory_size(appdata_out),
    }


def copy_support_files(app_dir: Path, app_out_dir: Path) -> list[str]:
    """Copy app-local build contexts/env files needed by compose, excluding handled files."""
    copied: list[str] = []
    compose_names = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
    for item in app_dir.iterdir():
        if item.name in compose_names or item.name == "icon.png":
            continue
        dest = app_out_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True, symlinks=True)
            copied.append(f"{item.name}/")
        elif item.is_file():
            shutil.copy2(item, dest)
            copied.append(item.name)
    return copied


def copy_path_to_appdata(source: Path) -> dict[str, Any]:
    """Copy any /DATA/AppData path into project appdata preserving relative layout."""
    try:
        relative = source.resolve().relative_to(CASAOS_APPDATA_DIR)
    except ValueError:
        return {"source": str(source), "path": None, "status": "skipped", "reason": "outside CasaOS AppData"}
    dest = DASHBOARD_APPDATA_DIR / relative
    if not source.exists():
        return {"source": str(source), "path": str(dest), "status": "missing", "copied": False, "reason": "not found"}
    DASHBOARD_APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    rsync = shutil.which("rsync")
    if source.is_dir():
        if rsync:
            result = subprocess.run(
                [rsync, "-a", "--delete", f"{source}/", f"{dest}/"],
                check=False,
                text=True,
                capture_output=True,
                timeout=1800,
            )
            if result.returncode != 0:
                reason = (result.stderr or result.stdout or f"rsync exited {result.returncode}").strip()
                return {"source": str(source), "path": str(dest), "status": "partial", "copied": False, "reason": reason, "size_bytes": directory_size(dest) if dest.exists() else None}
        else:
            shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    return {"source": str(source), "path": str(dest), "status": "copied", "copied": True, "size_bytes": directory_size(dest) if dest.is_dir() else dest.stat().st_size}


def rewrite_compose_appdata_sources(compose_path: Path, app_id: str) -> list[dict[str, Any]]:
    """Rewrite compose bind sources from /DATA/AppData to project-owned appdata."""
    try:
        compose = load_yaml(compose_path)
    except Exception as exc:  # noqa: BLE001
        return [{"source": None, "path": None, "status": "failed", "reason": f"compose parse failed: {exc}"}]

    services = compose.get("services") or {}
    if not isinstance(services, dict):
        return []

    copied: list[dict[str, Any]] = []
    changed = False
    seen: set[str] = set()

    def migrate_source(raw: Any) -> str | None:
        nonlocal changed
        if not isinstance(raw, str):
            return None
        expanded = raw.replace("$AppID", app_id).replace("${AppID}", app_id)
        if not expanded.startswith(str(CASAOS_APPDATA_DIR) + "/"):
            return None
        source = Path(expanded)
        result = copy_path_to_appdata(source)
        if expanded not in seen:
            copied.append(result)
            seen.add(expanded)
        dest = result.get("path")
        if dest:
            changed = True
            return str(dest)
        return None

    for service in services.values():
        if not isinstance(service, dict):
            continue
        volumes = service.get("volumes") or []
        if not isinstance(volumes, list):
            continue
        for index, volume in enumerate(volumes):
            if isinstance(volume, dict):
                dest = migrate_source(volume.get("source"))
                if dest:
                    volume["source"] = dest
            elif isinstance(volume, str):
                parts = volume.split(":")
                dest = migrate_source(parts[0])
                if dest:
                    parts[0] = dest
                    volumes[index] = ":".join(parts)

    if changed:
        compose_path.write_text(yaml.safe_dump(compose, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return copied


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
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
                source TEXT NOT NULL DEFAULT 'casaos'
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(app)")}
        if "appdata_path" not in columns:
            conn.execute("ALTER TABLE app ADD COLUMN appdata_path TEXT")
        conn.commit()


def upsert_app(meta: dict[str, Any], compose_path: Path) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO app (
                id, name, description, category, icon_path, web_ui_port,
                web_ui_path, compose_path, appdata_path, enabled, added_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                category=excluded.category,
                icon_path=excluded.icon_path,
                web_ui_port=excluded.web_ui_port,
                web_ui_path=excluded.web_ui_path,
                compose_path=excluded.compose_path,
                appdata_path=excluded.appdata_path,
                enabled=1,
                source=excluded.source
            """,
            (
                meta["id"],
                meta["name"],
                meta.get("description"),
                meta.get("category"),
                meta.get("icon"),
                meta.get("web_ui_port"),
                meta.get("web_ui_path", "/"),
                str(compose_path),
                meta.get("appdata_path"),
                meta["added_at"],
                meta.get("source", "casaos"),
            ),
        )
        conn.commit()


def migrate_app(app_dir: Path) -> dict[str, Any]:
    app_id = app_dir.name
    compose_in = find_compose_path(app_dir)
    if compose_in is None:
        return {"id": app_id, "status": "skipped", "reason": "no compose file"}

    compose = load_yaml(compose_in)
    casaos = compose.get("x-casaos") or {}
    if not isinstance(casaos, dict):
        casaos = {}
    main_service_name, service = choose_main_service(compose, casaos)

    app_out_dir = DASHBOARD_APPS_DIR / app_id
    app_out_dir.mkdir(parents=True, exist_ok=True)
    support_files = copy_support_files(app_dir, app_out_dir)
    compose_out = app_out_dir / "docker-compose.yml"

    resolved = get_resolved_compose(app_id)
    if resolved:
        compose_out.write_text(resolved, encoding="utf-8")
        # Use resolved content for metadata if it still parses cleanly.
        try:
            compose = yaml.safe_load(resolved) or compose
            if isinstance(compose, dict):
                casaos = compose.get("x-casaos") or casaos
                main_service_name, service = choose_main_service(compose, casaos if isinstance(casaos, dict) else {})
        except yaml.YAMLError:
            pass
    else:
        shutil.copy2(compose_in, compose_out)

    web_port = determine_web_port(casaos, service)
    web_path = localized(casaos.get("index")) or localized(casaos.get("web_ui_path")) or "/"
    if not web_path.startswith("/"):
        web_path = f"/{web_path}"

    icon_found = resolve_icon(app_id, app_dir, app_out_dir, casaos, service)
    appdata = copy_appdata(app_id)
    volume_appdata = rewrite_compose_appdata_sources(compose_out, app_id)
    primary_appdata_path = appdata.get("path") or next((item.get("path") for item in volume_appdata if item.get("path")), None)
    name = localized(casaos.get("title")) or localized(casaos.get("name")) or app_id.replace("-", " ").title()
    description = localized(casaos.get("description")) or localized(casaos.get("tagline"))

    meta = {
        "id": app_id,
        "name": name,
        "description": description,
        "category": localized(casaos.get("category")),
        "author": localized(casaos.get("author")) or localized(casaos.get("developer")),
        "icon": "icon.png" if icon_found else None,
        "web_ui_port": web_port,
        "web_ui_path": web_path,
        "appdata_path": primary_appdata_path,
        "appdata_source": appdata.get("source"),
        "appdata_status": appdata.get("status"),
        "appdata_size_bytes": appdata.get("size_bytes"),
        "volume_appdata": volume_appdata,
        "added_at": utc_now(),
        "source": "casaos",
        "main_service": main_service_name,
        "support_files": support_files,
    }

    (app_out_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    upsert_app(meta, compose_out)

    return {
        "id": app_id,
        "status": "migrated",
        "icon": icon_found,
        "web_ui_port": web_port,
        "name": name,
        "appdata": appdata,
    }


def main() -> int:
    if not CASAOS_APPS_DIR.exists():
        print(f"CasaOS apps directory does not exist: {CASAOS_APPS_DIR}", file=sys.stderr)
        return 1

    DASHBOARD_APPS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    results: list[dict[str, Any]] = []
    for app_dir in sorted(CASAOS_APPS_DIR.iterdir(), key=lambda p: p.name):
        if not app_dir.is_dir():
            continue
        try:
            results.append(migrate_app(app_dir))
        except Exception as exc:  # noqa: BLE001 - one bad app should not abort the migration.
            results.append({"id": app_dir.name, "status": "skipped", "reason": str(exc)})
            print(f"Warning: failed to migrate {app_dir.name}: {exc}", file=sys.stderr)

    migrated = [r for r in results if r.get("status") == "migrated"]
    skipped = [r for r in results if r.get("status") == "skipped"]
    icons_found = sum(1 for r in migrated if r.get("icon"))

    report = {
        "generated_at": utc_now(),
        "source_dir": str(CASAOS_APPS_DIR),
        "appdata_source_dir": str(CASAOS_APPDATA_DIR),
        "apps_dir": str(DASHBOARD_APPS_DIR),
        "appdata_dir": str(DASHBOARD_APPDATA_DIR),
        "database": str(DB_PATH),
        "migrated_count": len(migrated),
        "skipped_count": len(skipped),
        "icons_found": icons_found,
        "icons_total": len(migrated),
        "migrated_apps": migrated,
        "skipped_apps": skipped,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    migrated_names = ", ".join(r["id"] for r in migrated) or "none"
    print(f"Migrated {len(migrated)} apps: {migrated_names}")
    print(f"Skipped {len(skipped)} apps")
    if skipped:
        for item in skipped:
            print(f"- skipped {item['id']}: {item.get('reason', 'unknown reason')}")
    print(f"Icons found: {icons_found}/{len(migrated)}")
    print(f"Report written: {REPORT_PATH}")
    return 0 if migrated else 2


if __name__ == "__main__":
    raise SystemExit(main())
