#!/usr/bin/env python3
"""Source-preserving, resumable migration into the production storage contract.

The command never removes a source.  A dry run performs no writes or service
actions.  Non-dry runs back up destinations before changing them and restore
those backups when a migration step fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.paths import from_environment

COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")

@dataclass
class MigrationResult:
    exit_code: int
    message: str
    operations: list[str] = field(default_factory=list)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def storage_is_mounted(path: Path, expected_uuid: str | None = None) -> bool:
    """Require a real mount and, when configured, its exact filesystem UUID."""
    if not expected_uuid:
        return False
    try:
        if subprocess.run(["mountpoint", "-q", str(path)], check=False).returncode != 0:
            return False
        result = subprocess.run(["findmnt", "-n", "-o", "UUID", "--target", str(path)], check=False,
                                capture_output=True, text=True)
        return result.returncode == 0 and result.stdout.strip() == expected_uuid
    except OSError:
        return False


def _rsync(source: Path, destination: Path, dry_run: bool, operations: list[str]) -> None:
    command = [shutil.which("rsync") or "rsync", "-aHAX", "--numeric-ids"]
    if dry_run:
        command.append("--dry-run")
    command.extend([f"{source}/", f"{destination}/"])
    operations.append(" ".join(command))
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(command, check=False, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "rsync failed").strip())


def _copy_tree(source: Path, destination: Path, dry_run: bool, operations: list[str]) -> None:
    operations.append(f"copy {source} -> {destination}")
    if dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, copy_function=shutil.copy2, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination)


def _backup_destination(destination: Path, backup_root: Path, backup_identity: str,
                        dry_run: bool, operations: list[str]) -> Path | None:
    if not destination.exists():
        operations.append(f"destination backup {destination} -> none (destination absent)")
        return None
    backup = backup_root / backup_identity
    operations.append(f"destination backup {destination} -> {backup}")
    if not dry_run:
        _copy_tree(destination, backup, False, [])
    return backup


def _backup_identity(destination: Path, index: int) -> str:
    """Return a unique, stable backup name for the complete destination identity."""
    digest = hashlib.sha256(str(destination.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"destination-{index:02d}-{digest}"


def _new_backup_root(state: Path, dry_run: bool) -> Path:
    """Allocate an exclusive per-invocation namespace for destination backups."""
    parent = state.parent / "lab-dashboard-migration-backups"
    stamp = _stamp()
    while True:
        candidate = parent / f"{stamp}-{uuid.uuid4().hex}"
        if dry_run:
            return candidate
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return candidate


def _write_backup_manifest(backup_root: Path, plans: list[tuple[Path, Path, Path]],
                           backups: dict[Path, Path | None], dry_run: bool) -> None:
    """Persist canonical source/destination mappings and their destination backups."""
    if dry_run:
        return
    backup_root.mkdir(parents=True, exist_ok=True)
    destination_records = []
    for source, destination, backup_destination in plans:
        backup = backups[backup_destination]
        destination_records.append({
            "source": str(source.resolve()),
            "destination": str(destination.resolve()),
            "backup": str(backup.resolve()) if backup is not None else None,
        })
    manifest = {
        "destinations": destination_records,
    }
    (backup_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _restore_destination(destination: Path, backup: Path | None) -> None:
    if backup is None:
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
        return
    if destination.is_dir():
        shutil.rmtree(destination)
    elif destination.exists():
        destination.unlink()
    _copy_tree(backup, destination, False, [])


def _compose_source(app_dir: Path) -> Path | None:
    return next((app_dir / name for name in COMPOSE_NAMES if (app_dir / name).is_file()), None)


_DASHBOARD_LEGACY_ROOTS = (
    "/DATA/nas/Projects/dashboard",
    "/home/juyoungoh/nas/Projects/dashboard",
)
_NAS_LEGACY_ROOTS = ("/DATA/nas", "/home/juyoungoh/nas")


def _rewrite_legacy_host_path(value: str, source: Path) -> str:
    """Map only approved legacy host bind roots, refusing unsafe stale paths."""
    for root in _DASHBOARD_LEGACY_ROOTS:
        if value == root or value.startswith(root + "/"):
            suffix = value[len(root):].lstrip("/")
            if suffix == "appdata" or suffix.startswith("appdata/"):
                return "/srv/appdata" + ("/" + suffix[len("appdata/"):] if "/" in suffix else "")
            raise ValueError(f"unmappable legacy dashboard bind in {source}: {value}")
    if value == "/DATA/nas/Projects" or value.startswith("/DATA/nas/Projects/"):
        raise ValueError(f"unmappable legacy Projects bind in {source}: {value}")
    if value == "/home/juyoungoh/nas/Projects" or value.startswith("/home/juyoungoh/nas/Projects/"):
        raise ValueError(f"unmappable legacy Projects bind in {source}: {value}")
    for root in _NAS_LEGACY_ROOTS:
        if value == root or value.startswith(root + "/"):
            suffix = value[len(root):].lstrip("/")
            if suffix.startswith("AppData/"):
                return "/srv/appdata/" + suffix[len("AppData/"):]
            return "/srv/storage/nas/" + suffix
    if value.startswith("/DATA/AppData/"):
        return "/srv/appdata/" + value[len("/DATA/AppData/"):]
    return value


def _rewrite_compose_volumes(data: dict, source: Path) -> dict:
    """Rewrite Compose volume host sources without touching container values."""
    for service in (data.get("services") or {}).values():
        if not isinstance(service, dict):
            continue
        volumes = service.get("volumes") or []
        rewritten = []
        for volume in volumes:
            if isinstance(volume, dict):
                item = dict(volume)
                host = item.get("source")
                if isinstance(host, str):
                    item["source"] = _rewrite_legacy_host_path(host, source)
                rewritten.append(item)
                continue
            if not isinstance(volume, str):
                rewritten.append(volume)
                continue
            parts = volume.split(":")
            if len(parts) >= 2 and parts[0].startswith(("/", "~")):
                parts[0] = _rewrite_legacy_host_path(parts[0], source)
                rewritten.append(":".join(parts))
            else:
                rewritten.append(volume)
        service["volumes"] = rewritten
    return data


def _canonicalize_compose(source: Path, destination: Path, operations: list[str], dry_run: bool) -> None:
    """Copy a Compose definition to the sole managed filename, compose.yaml."""
    operations.append(f"transform {source} -> {destination}")
    # Parse and validate even for dry-runs so unsafe legacy binds fail closed.
    import yaml
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise ValueError(f"invalid Compose definition: {source}")
    data = _rewrite_compose_volumes(data, source)
    if dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _transform_database(source_db: Path, destination_db: Path, stacks: Path, operations: list[str], dry_run: bool) -> None:
    """Copy SQLite transactionally and rewrite every app path to canonical stacks."""
    operations.append(f"transform database {source_db} -> {destination_db}")
    if dry_run:
        return
    destination_db.parent.mkdir(parents=True, exist_ok=True)
    temp = destination_db.with_suffix(destination_db.suffix + ".migration-tmp")
    shutil.copy2(source_db, temp)
    try:
        conn = sqlite3.connect(temp)
        conn.execute("BEGIN")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(app)")}
        if "compose_path" not in columns:
            raise ValueError("app table has no compose_path column")
        rows = conn.execute("SELECT id, compose_path FROM app").fetchall()
        for app_id, old_path in rows:
            canonical = stacks / str(app_id) / "compose.yaml"
            conn.execute("UPDATE app SET compose_path = ? WHERE id = ?", (str(canonical), app_id))
            if old_path and not canonical.exists():
                raise FileNotFoundError(f"canonical Compose missing for {app_id}: {canonical}")
        conn.commit()
        conn.close()
        os.replace(temp, destination_db)
    except Exception:
        if temp.exists():
            temp.unlink()
        raise


def _migrate_syncthing_config(source: Path, destination: Path, operations: list[str], dry_run: bool) -> None:
    """Safely mutate folder paths by stable path, preserving identity and peers.

    Projects is explicitly disabled rather than deleted, making the operation
    reversible and preventing either old or new Projects path from syncing.
    """
    import xml.etree.ElementTree as ET
    operations.append(f"syncthing mapping {source} -> {destination}")
    tree = ET.parse(source)
    root = tree.getroot()
    folders = root.findall("./folder")
    university = [f for f in folders if f.get("path") in {
        "/DATA/nas/University", "/home/juyoungoh/nas/University", "/srv/storage/nas/University"
    }]
    projects = [f for f in folders if f.get("path") in {
        "/DATA/nas/Projects", "/home/juyoungoh/nas/Projects", "/srv/storage/nas/Projects"
    }]
    recognized = {folder.get("path") for folder in university + projects}
    unknown_legacy = []
    for folder in folders:
        path = folder.get("path")
        if isinstance(path, str) and (
            path.startswith("/DATA/nas/") or path.startswith("/home/juyoungoh/nas/")
        ) and path not in recognized:
            unknown_legacy.append(path)
    if unknown_legacy:
        raise ValueError(f"unknown legacy Syncthing folder mapping: {unknown_legacy[0]}")
    if len(university) > 1 or len(projects) > 1:
        raise ValueError("ambiguous Syncthing University or Projects folder mapping")
    if not university and not projects:
        raise ValueError("no recognized Syncthing University/Projects folder mapping")
    if university:
        university[0].set("path", "/srv/storage/nas/University")
    if projects:
        projects[0].set("disabled", "true")
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        tree.write(destination, encoding="utf-8", xml_declaration=True)


def _syncthing_config_source(source: Path) -> Path | None:
    candidates = (
        source / "appdata" / "big-bear-syncthing" / "config" / "config.xml",
        source / "appdata" / "big-bear-syncthing" / "config.xml",
        source / "appdata" / "big-bear-syncthing" / "data" / "config.xml",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def run_migration(*, source: Path, state: Path, stacks: Path, appdata: Path, storage: Path,
                  mount_uuid: str | None, dry_run: bool,
                  mount_probe: Callable[[Path, str | None], bool] = storage_is_mounted,
                  service_unit: str | None = None, service_runner: Callable[..., object] = subprocess.run) -> MigrationResult:
    operations: list[str] = []
    if not mount_probe(storage, mount_uuid):
        return MigrationResult(2, f"Required storage mount {storage} is not mounted or UUID does not match", operations)
    if not source.exists():
        return MigrationResult(2, f"source does not exist: {source}", operations)
    # Keep the backup tree outside the state destination being backed up; placing
    # it below ``state`` would recursively copy the backup into itself.
    backup_root = _new_backup_root(state, dry_run)
    plans = [(source / "data", state), (source / "appdata", appdata)]
    compose_sources = []
    source_apps = source / "apps"
    if source_apps.exists():
        for app_dir in sorted(p for p in source_apps.iterdir() if p.is_dir()):
            compose = _compose_source(app_dir)
            if compose:
                compose_sources.append((compose, stacks / app_dir.name / "compose.yaml"))
    manifest_plans = ([(old, new, new) for old, new in plans]
                      + [(old, new, new.parent) for old, new in compose_sources])
    destination_paths = [backup_destination for _old, _new, backup_destination in manifest_plans]
    unique_destinations = list(dict.fromkeys(destination_paths))
    backups: dict[Path, Path | None] = {}
    stopped = False
    restart_error: Exception | None = None
    try:
        for index, destination in enumerate(unique_destinations):
            identity = _backup_identity(destination, index)
            backups[destination] = _backup_destination(destination, backup_root, identity, dry_run, operations)
        _write_backup_manifest(backup_root, manifest_plans, backups, dry_run)
        if service_unit and unique_destinations:
            operations.append(f"systemctl stop {service_unit}")
            if not dry_run:
                service_runner(["systemctl", "stop", service_unit], check=True)
                stopped = True
        for old, new in plans:
            if old.exists() and old.is_dir():
                _rsync(old, new, dry_run, operations)
        for old, new in compose_sources:
            _canonicalize_compose(old, new, operations, dry_run)
        syncthing_source = _syncthing_config_source(source)
        if syncthing_source:
            relative = syncthing_source.relative_to(source / "appdata")
            _migrate_syncthing_config(
                syncthing_source,
                appdata / relative,
                operations,
                dry_run,
            )
        source_db = source / "data" / "dashboard.db"
        if source_db.exists():
            _transform_database(source_db, state / "dashboard.db", stacks, operations, dry_run)
        operations.append(f"ensure directory {storage / 'nas'}")
        if not dry_run:
            (storage / "nas").mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        if not dry_run:
            for destination, backup in reversed(list(backups.items())):
                try:
                    _restore_destination(destination, backup)
                except Exception as rollback_exc:
                    operations.append(f"ROLLBACK FAILED {destination}: {rollback_exc}")
        return MigrationResult(1, f"migration failed: {exc}; destinations restored where possible", operations)
    finally:
        if stopped:
            try:
                service_runner(["systemctl", "start", service_unit], check=True)
                operations.append(f"systemctl start {service_unit}")
            except Exception as exc:
                operations.append(f"RESTART FAILED {service_unit}: {exc}")
                restart_error = exc
    if restart_error is not None:
        return MigrationResult(1, f"migration completed but service restart failed: {restart_error}", operations)
    return MigrationResult(0, "dry-run complete" if dry_run else "migration complete", operations)


def main(argv: list[str] | None = None) -> int:
    paths = from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=paths.root)
    parser.add_argument("--state", type=Path, default=paths.state)
    parser.add_argument("--stacks", type=Path, default=paths.stacks)
    parser.add_argument("--appdata", type=Path, default=paths.appdata)
    parser.add_argument("--storage", type=Path, default=paths.nas.parent)
    parser.add_argument("--storage-uuid", default=os.environ.get("DASHBOARD_STORAGE_UUID"))
    parser.add_argument("--service-unit", default="lab-dashboard.service")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_migration(source=args.source, state=args.state, stacks=args.stacks, appdata=args.appdata,
                               storage=args.storage, mount_uuid=args.storage_uuid, dry_run=args.dry_run,
                               service_unit=args.service_unit)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    for operation in result.operations:
        print(("DRY-RUN: " if args.dry_run else "") + operation)
    print(result.message)
    return result.exit_code

if __name__ == "__main__":
    raise SystemExit(main())
