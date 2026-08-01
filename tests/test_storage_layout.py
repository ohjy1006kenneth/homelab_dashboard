from __future__ import annotations

import os
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
