from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from scripts.migrate_storage_layout import _canonicalize_compose, _migrate_syncthing_config, run_migration
from backend.storage import compose_requires_storage, storage_mount_is_safe
from backend.routers import apps


def test_compose_transform_and_database_paths(tmp_path):
    source = tmp_path / "old"
    app = source / "apps" / "demo"
    app.mkdir(parents=True)
    (app / "docker-compose.yml").write_text("services:\n  demo:\n    image: alpine\n", encoding="utf-8")
    data = source / "data"
    data.mkdir()
    db = data / "dashboard.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE app (id TEXT PRIMARY KEY, compose_path TEXT NOT NULL)")
        conn.execute("INSERT INTO app VALUES (?, ?)", ("demo", str(app / "docker-compose.yml")))
        conn.commit()
    storage = tmp_path / "storage"
    storage.mkdir()
    result = run_migration(source=source, state=tmp_path / "state", stacks=tmp_path / "stacks",
                           appdata=tmp_path / "appdata", storage=storage, mount_uuid="fixture",
                           dry_run=False, mount_probe=lambda *_: True)
    assert result.exit_code == 0
    canonical = tmp_path / "stacks" / "demo" / "compose.yaml"
    assert canonical.exists()
    with sqlite3.connect(tmp_path / "state" / "dashboard.db") as conn:
        assert conn.execute("SELECT compose_path FROM app").fetchone()[0] == str(canonical)
    assert (app / "docker-compose.yml").exists()


def test_syncthing_mapping_preserves_identity_and_disables_projects(tmp_path):
    source = tmp_path / "config.xml"
    destination = tmp_path / "new.xml"
    source.write_text('''<configuration><folder id="uni" path="/DATA/nas/University" rescanIntervalS="60"/><folder id="projects" path="/srv/storage/nas/Projects" disabled="false"/><device id="peer">x</device><gui enabled="true"/></configuration>''', encoding="utf-8")
    operations = []
    _migrate_syncthing_config(source, destination, operations, False)
    root = ET.parse(destination).getroot()
    folders = {node.get("id"): node for node in root.findall("folder")}
    assert folders["uni"].get("path") == "/srv/storage/nas/University"
    assert folders["projects"].get("disabled") == "true"
    device = root.find("device")
    gui = root.find("gui")
    assert device is not None and device.get("id") == "peer"
    assert gui is not None and gui.get("enabled") == "true"


def test_storage_bind_detection_is_scoped():
    assert compose_requires_storage({"services": {"x": {"volumes": ["/srv/storage/nas/University:/data"]}}})
    assert not compose_requires_storage({"services": {"x": {"volumes": ["/srv/appdata/x:/data"]}}})


@pytest.mark.parametrize(
    ("mount_rc", "findmnt_rc", "uuid", "expected"),
    [(1, 0, "fixture", False), (0, 1, "fixture", False),
     (0, 0, "fixture", True), (0, 0, "other", False)],
)
def test_storage_mount_probe_checks_mount_and_exact_uuid(
    monkeypatch, tmp_path, mount_rc, findmnt_rc, uuid, expected
):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[0] == "mountpoint":
            return subprocess.CompletedProcess(command, mount_rc)
        return subprocess.CompletedProcess(command, findmnt_rc, stdout=f"{uuid}\n")

    monkeypatch.setattr("backend.storage.subprocess.run", run)
    assert storage_mount_is_safe(tmp_path / "storage", "fixture") is expected
    assert calls and calls[0][0] == "mountpoint"
    if mount_rc == 0:
        assert calls[1][0] == "findmnt"


@pytest.mark.parametrize("uuid", [None, "mismatched-uuid"])
def test_storage_dependent_api_refuses_before_docker(tmp_path, monkeypatch, uuid):
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("services:\n  app:\n    volumes: ['/srv/storage/nas:/data']\n", encoding="utf-8")
    monkeypatch.setattr(apps, "_app_row", lambda _app_id: {"compose_path": str(compose_path), "web_ui_port": None})
    monkeypatch.setattr("backend.storage.storage_mount_is_safe", lambda: False)
    docker_calls = []
    monkeypatch.setattr(apps.subprocess, "run", lambda *args, **kwargs: docker_calls.append(args) or None)

    with pytest.raises(HTTPException) as error:
        apps._run_compose("demo", ["up"])

    assert error.value.status_code == 503
    assert docker_calls == []


def test_compose_rewrites_short_long_and_preserves_non_host_values(tmp_path):
    source = tmp_path / "legacy compose.yml"
    destination = tmp_path / "out" / "compose.yaml"
    source.write_text("""services:
  app:
    image: alpine
    command: [\"/DATA/nas/Projects/dashboard/appdata/not-a-bind\"]
    volumes:
      - /home/juyoungoh/nas/Projects/dashboard/appdata/demo:/var/lib/demo:ro
      - type: bind
        source: /DATA/nas/University
        target: /data
      - named_volume:/named
""", encoding="utf-8")

    _canonicalize_compose(source, destination, [], False)
    data = yaml.safe_load(destination.read_text(encoding="utf-8"))
    volumes = data["services"]["app"]["volumes"]
    assert volumes[0] == "/srv/appdata/demo:/var/lib/demo:ro"
    assert volumes[1]["source"] == "/srv/storage/nas/University"
    assert volumes[1]["target"] == "/data"
    assert volumes[2] == "named_volume:/named"
    assert data["services"]["app"]["command"] == ["/DATA/nas/Projects/dashboard/appdata/not-a-bind"]


def test_compose_rejects_unmappable_projects_bind_even_on_dry_run(tmp_path):
    source = tmp_path / "compose.yml"
    source.write_text("services:\n  app:\n    image: alpine\n    volumes: ['/DATA/nas/Projects/foo:/data']\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unmappable legacy Projects bind"):
        _canonicalize_compose(source, tmp_path / "out.yaml", [], True)


def test_syncthing_rejects_unknown_legacy_mapping(tmp_path):
    source = tmp_path / "config.xml"
    source.write_text('<configuration><folder id="uni" path="/DATA/nas/University"/><folder id="other" path="/DATA/nas/Unreviewed"/></configuration>', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown legacy Syncthing folder mapping"):
        _migrate_syncthing_config(source, tmp_path / "new.xml", [], True)


def test_casaos_cli_uses_storage_contract_from_arbitrary_cwd(tmp_path):
    casaos_apps = tmp_path / "casaos-apps" / "demo"
    casaos_apps.mkdir(parents=True)
    (casaos_apps / "docker-compose.yml").write_text(
        "services:\n  demo:\n    image: alpine\n    ports: ['8080:80']\n",
        encoding="utf-8",
    )
    root = tmp_path / "root"
    state = tmp_path / "state"
    stacks = tmp_path / "stacks"
    appdata = tmp_path / "appdata"
    env = os.environ.copy()
    env.update({
        "CASAOS_APPS_DIR": str(tmp_path / "casaos-apps"),
        "DASHBOARD_ROOT": str(root),
        "DASHBOARD_STATE_DIR": str(state),
        "DASHBOARD_STACKS_DIR": str(stacks),
        "DASHBOARD_APPDATA_DIR": str(appdata),
    })
    for name in ("DASHBOARD_NAS_DIR", "DASHBOARD_STORAGE_UUID"):
        env.pop(name, None)

    result = subprocess.run(
        [sys.executable, str(_repo_root() / "scripts" / "migrate_from_casaos.py")],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (root / "apps" / "demo" / "meta.json").exists()
    assert (stacks / "demo" / "compose.yaml").exists()
    assert (state / "dashboard.db").exists()
    assert not (tmp_path / "apps").exists()


def _repo_root():
    return Path(__file__).resolve().parents[1]


def test_run_migration_executes_syncthing_and_dry_run_writes_nothing(tmp_path):
    source = tmp_path / "old"
    config = source / "appdata" / "big-bear-syncthing" / "config" / "config.xml"
    config.parent.mkdir(parents=True)
    config.write_text('<configuration><folder id="uni" path="/home/juyoungoh/nas/University"/><folder id="projects" path="/home/juyoungoh/nas/Projects"/><device id="peer"/><gui enabled="true"/><options><listenAddress>tcp://0.0.0.0:22000</listenAddress></options></configuration>', encoding="utf-8")
    storage = tmp_path / "storage"
    storage.mkdir()
    result = run_migration(source=source, state=tmp_path / "state", stacks=tmp_path / "stacks",
                           appdata=tmp_path / "appdata", storage=storage, mount_uuid="fixture",
                           dry_run=True, mount_probe=lambda *_: True)
    assert result.exit_code == 0
    assert any("syncthing mapping" in operation for operation in result.operations)
    assert not (tmp_path / "appdata").exists()

    result = run_migration(source=source, state=tmp_path / "state", stacks=tmp_path / "stacks",
                           appdata=tmp_path / "appdata", storage=storage, mount_uuid="fixture",
                           dry_run=False, mount_probe=lambda *_: True)
    assert result.exit_code == 0
    migrated = tmp_path / "appdata" / "big-bear-syncthing" / "config" / "config.xml"
    root = ET.parse(migrated).getroot()
    folders = {node.get("id"): node for node in root.findall("folder")}
    assert folders["uni"].get("path") == "/srv/storage/nas/University"
    assert folders["projects"].get("disabled") == "true"
    assert root.find("device").get("id") == "peer"
    assert root.find("gui").get("enabled") == "true"
    assert root.find("options/listenAddress").text == "tcp://0.0.0.0:22000"
