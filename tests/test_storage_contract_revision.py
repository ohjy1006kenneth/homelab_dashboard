from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET

from scripts.migrate_storage_layout import _migrate_syncthing_config, run_migration
from backend.storage import compose_requires_storage


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
