from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from backend import paths
from scripts import migrate_storage_layout


def test_production_paths_are_explicit(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ROOT", "/opt/lab-dashboard")
    monkeypatch.setenv("DASHBOARD_STATE_DIR", "/var/lib/lab-dashboard")
    monkeypatch.setenv("DASHBOARD_STACKS_DIR", "/srv/docker/stacks")
    monkeypatch.setenv("DASHBOARD_APPDATA_DIR", "/srv/appdata")
    monkeypatch.setenv("DASHBOARD_NAS_DIR", "/srv/storage/nas")

    configured = paths.from_environment()

    assert configured.root == Path("/opt/lab-dashboard")
    assert configured.state == Path("/var/lib/lab-dashboard")
    assert configured.stacks == Path("/srv/docker/stacks")
    assert configured.appdata == Path("/srv/appdata")
    assert configured.nas == Path("/srv/storage/nas")
    assert configured.db == Path("/var/lib/lab-dashboard/dashboard.db")


def test_production_paths_do_not_depend_on_checkout_or_cwd(monkeypatch, tmp_path):
    for name in ("DASHBOARD_ROOT", "DASHBOARD_STATE_DIR", "DASHBOARD_STACKS_DIR",
                 "DASHBOARD_APPDATA_DIR", "DASHBOARD_NAS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    configured = paths.from_environment()

    assert configured.root == Path("/opt/lab-dashboard")
    assert configured.state == Path("/var/lib/lab-dashboard")
    assert configured.stacks == Path("/srv/docker/stacks")
    assert configured.appdata == Path("/srv/appdata")
    assert configured.nas == Path("/srv/storage/nas")


def test_migration_dry_run_fails_closed_without_mount(tmp_path):
    result = migrate_storage_layout.run_migration(
        source=tmp_path / "old",
        state=tmp_path / "state",
        stacks=tmp_path / "stacks",
        appdata=tmp_path / "appdata",
        storage=tmp_path / "storage",
        mount_uuid="TEST-UUID",
        dry_run=True,
        mount_probe=lambda _path, _uuid: False,
    )

    assert result.exit_code != 0
    assert "not mounted" in result.message.lower()
    assert not (tmp_path / "state").exists()


def test_migration_dry_run_is_non_destructive_and_plans_backups(tmp_path):
    source = tmp_path / "old"
    storage = tmp_path / "storage"
    (source / "data").mkdir(parents=True)
    (source / "data" / "dashboard.db").write_text("db", encoding="utf-8")
    storage.mkdir()

    result = migrate_storage_layout.run_migration(
        source=source,
        state=tmp_path / "state",
        stacks=tmp_path / "stacks",
        appdata=tmp_path / "appdata",
        storage=storage,
        mount_uuid="TEST-UUID",
        dry_run=True,
        mount_probe=lambda _path, _uuid: True,
    )

    assert result.exit_code == 0
    assert result.operations
    assert any("backup" in operation.lower() for operation in result.operations)
    assert (source / "data" / "dashboard.db").read_text(encoding="utf-8") == "db"
    assert not (tmp_path / "state").exists()


def test_migration_manifest_records_exact_source_destination_and_backup_provenance(tmp_path):
    source = tmp_path / "old"
    storage = tmp_path / "storage"
    (source / "data").mkdir(parents=True)
    (source / "data" / "marker").write_text("source-data", encoding="utf-8")
    (source / "appdata").mkdir()
    (source / "appdata" / "marker").write_text("source-appdata", encoding="utf-8")
    compose_source = source / "apps" / "demo" / "docker-compose.yml"
    compose_source.parent.mkdir(parents=True)
    compose_source.write_text("services:\n  demo:\n    image: alpine\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    (state / "existing").write_text("preexisting", encoding="utf-8")
    storage.mkdir()

    result = migrate_storage_layout.run_migration(
        source=source, state=state, stacks=tmp_path / "stacks", appdata=tmp_path / "appdata",
        storage=storage, mount_uuid="fixture", dry_run=False, mount_probe=lambda *_: True,
    )

    assert result.exit_code == 0
    backup_operation = next(op for op in result.operations if "destination backup" in op)
    backup_path = Path(backup_operation.split(" -> ")[-1])
    manifest = json.loads((backup_path.parent / "manifest.json").read_text(encoding="utf-8"))
    records = {Path(item["destination"]): item for item in manifest["destinations"]}
    assert records[state] == {
        "source": str((source / "data").resolve()),
        "destination": str(state.resolve()),
        "backup": str(backup_path.resolve()),
    }
    assert records[tmp_path / "appdata"] == {
        "source": str((source / "appdata").resolve()),
        "destination": str((tmp_path / "appdata").resolve()),
        "backup": None,
    }
    compose_destination = tmp_path / "stacks" / "demo" / "compose.yaml"
    assert records[compose_destination] == {
        "source": str(compose_source.resolve()),
        "destination": str(compose_destination.resolve()),
        "backup": None,
    }
    assert (source / "data" / "marker").read_text(encoding="utf-8") == "source-data"
    assert (source / "appdata" / "marker").read_text(encoding="utf-8") == "source-appdata"
    assert compose_source.read_text(encoding="utf-8").startswith("services:")


def test_migration_rolls_back_colliding_destination_basenames_exactly(tmp_path):
    source = tmp_path / "old"
    storage = tmp_path / "storage"
    (source / "data").mkdir(parents=True)
    (source / "data" / "new.txt").write_text("new-state", encoding="utf-8")
    (source / "apps" / "state").mkdir(parents=True)
    (source / "apps" / "state" / "compose.yaml").write_text("services:\n  state:\n    image: alpine\n", encoding="utf-8")
    config = source / "appdata" / "big-bear-syncthing" / "config" / "config.xml"
    config.parent.mkdir(parents=True)
    config.write_text('<configuration><folder id="unknown" path="/DATA/nas/Unreviewed"/></configuration>', encoding="utf-8")
    state = tmp_path / "state"
    stack_state = tmp_path / "stacks" / "state"
    state.mkdir(parents=True)
    stack_state.mkdir(parents=True)
    (state / "state-only").write_text("state", encoding="utf-8")
    (stack_state / "stack-only").write_text("stack", encoding="utf-8")
    storage.mkdir()

    result = migrate_storage_layout.run_migration(
        source=source, state=state, stacks=tmp_path / "stacks", appdata=tmp_path / "appdata",
        storage=storage, mount_uuid="fixture", dry_run=False, mount_probe=lambda *_: True,
    )

    assert result.exit_code == 1
    assert sorted(p.name for p in state.iterdir()) == ["state-only"]
    assert sorted(p.name for p in stack_state.iterdir()) == ["stack-only"]
    backup_ops = [op for op in result.operations if "destination backup" in op and "-> none" not in op]
    assert len(backup_ops) == 2
    assert backup_ops[0].split(" -> ")[-1] != backup_ops[1].split(" -> ")[-1]


@pytest.mark.parametrize("app_id", ["appdata", "state"])
def test_migration_backup_identity_handles_additional_basename_collisions(tmp_path, app_id):
    source = tmp_path / "old"
    storage = tmp_path / "storage"
    (source / "data").mkdir(parents=True)
    with sqlite3.connect(source / "data" / "dashboard.db") as conn:
        conn.execute("CREATE TABLE app (id TEXT PRIMARY KEY, compose_path TEXT NOT NULL)")
        conn.commit()
    (source / "apps" / app_id).mkdir(parents=True)
    (source / "apps" / app_id / "compose.yaml").write_text("services:\n  app:\n    image: alpine\n", encoding="utf-8")
    destination = tmp_path / app_id
    destination.mkdir()
    (destination / "sentinel").write_text(app_id, encoding="utf-8")
    storage.mkdir()

    result = migrate_storage_layout.run_migration(
        source=source, state=tmp_path / "state", stacks=tmp_path / "stacks", appdata=tmp_path / "appdata",
        storage=storage, mount_uuid="fixture", dry_run=False, mount_probe=lambda *_: True,
    )

    assert result.exit_code == 0
    assert (destination / "sentinel").read_text(encoding="utf-8") == app_id


def test_migration_repeat_preserves_content_and_restarts_service(tmp_path):
    source = tmp_path / "old"
    (source / "data").mkdir(parents=True)
    (source / "data" / "marker").write_text("source", encoding="utf-8")
    storage = tmp_path / "storage"
    storage.mkdir()
    calls = []

    def service_runner(command, **_kwargs):
        calls.append(command)

    kwargs = dict(source=source, state=tmp_path / "state", stacks=tmp_path / "stacks",
                  appdata=tmp_path / "appdata", storage=storage, mount_uuid="fixture",
                  dry_run=False, mount_probe=lambda *_: True, service_unit="lab-dashboard.service",
                  service_runner=service_runner)
    first = migrate_storage_layout.run_migration(**kwargs)
    second = migrate_storage_layout.run_migration(**kwargs)

    assert first.exit_code == second.exit_code == 0
    assert (tmp_path / "state" / "marker").read_text(encoding="utf-8") == "source"
    assert calls == [["systemctl", "stop", "lab-dashboard.service"], ["systemctl", "start", "lab-dashboard.service"],
                     ["systemctl", "stop", "lab-dashboard.service"], ["systemctl", "start", "lab-dashboard.service"]]


def test_migration_backup_roots_are_unique_when_clock_is_fixed(tmp_path, monkeypatch):
    source = tmp_path / "old"
    (source / "data").mkdir(parents=True)
    (source / "data" / "marker").write_text("source", encoding="utf-8")
    storage = tmp_path / "storage"
    storage.mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "existing").write_text("state", encoding="utf-8")
    (tmp_path / "appdata").mkdir()
    (tmp_path / "appdata" / "existing").write_text("appdata", encoding="utf-8")
    monkeypatch.setattr(migrate_storage_layout, "_stamp", lambda: "fixed")

    kwargs = dict(source=source, state=tmp_path / "state", stacks=tmp_path / "stacks",
                  appdata=tmp_path / "appdata", storage=storage, mount_uuid="fixture",
                  dry_run=False, mount_probe=lambda *_: True)
    first = migrate_storage_layout.run_migration(**kwargs)
    second = migrate_storage_layout.run_migration(**kwargs)

    assert first.exit_code == second.exit_code == 0
    first_roots = {op.split(" -> ")[-1].rsplit("/destination-", 1)[0]
                   for op in first.operations if "destination backup" in op and "-> none" not in op}
    second_roots = {op.split(" -> ")[-1].rsplit("/destination-", 1)[0]
                    for op in second.operations if "destination backup" in op and "-> none" not in op}
    assert first_roots.isdisjoint(second_roots)


def test_migration_copy_failure_restores_partial_destinations_and_reports_restart_failure(tmp_path, monkeypatch):
    source = tmp_path / "old"
    (source / "data").mkdir(parents=True)
    (source / "data" / "new").write_text("new", encoding="utf-8")
    (source / "appdata").mkdir()
    (source / "appdata" / "new").write_text("new-app", encoding="utf-8")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "old").write_text("old", encoding="utf-8")
    (tmp_path / "appdata").mkdir()
    (tmp_path / "appdata" / "old").write_text("old-app", encoding="utf-8")
    storage = tmp_path / "storage"
    storage.mkdir()
    original_rsync = migrate_storage_layout._rsync
    rsync_calls = 0

    def failing_rsync(source_path, destination, dry_run, operations):
        nonlocal rsync_calls
        rsync_calls += 1
        if rsync_calls == 2:
            raise RuntimeError("injected copy failure")
        original_rsync(source_path, destination, dry_run, operations)

    service_calls = []

    def failing_restart(command, **_kwargs):
        service_calls.append(command)
        if command[1] == "start":
            raise RuntimeError("injected restart failure")

    monkeypatch.setattr(migrate_storage_layout, "_rsync", failing_rsync)
    result = migrate_storage_layout.run_migration(
        source=source, state=tmp_path / "state", stacks=tmp_path / "stacks", appdata=tmp_path / "appdata",
        storage=storage, mount_uuid="fixture", dry_run=False, mount_probe=lambda *_: True,
        service_unit="lab-dashboard.service", service_runner=failing_restart,
    )

    assert result.exit_code == 1
    assert "injected copy failure" in result.message
    assert (tmp_path / "state" / "old").read_text(encoding="utf-8") == "old"
    assert (tmp_path / "appdata" / "old").read_text(encoding="utf-8") == "old-app"
    assert service_calls == [["systemctl", "stop", "lab-dashboard.service"],
                             ["systemctl", "start", "lab-dashboard.service"]]
    assert any("RESTART FAILED" in operation for operation in result.operations)
