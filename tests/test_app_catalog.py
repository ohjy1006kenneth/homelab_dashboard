from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import backend.database as database
from backend.main import app


def _manifest(app_dir: Path, app_id: str, name: str, *, compose: bool = True) -> None:
    app_dir.mkdir(parents=True)
    (app_dir / "meta.json").write_text(
        json.dumps({"id": app_id, "name": name, "web_ui_port": 1234, "source": "fixture"}),
        encoding="utf-8",
    )
    if compose:
        (app_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: example\n", encoding="utf-8")


def test_apps_bootstrap_creates_table_and_reconciles_idempotently(monkeypatch, tmp_path):
    db_path = tmp_path / "dashboard.db"
    apps_dir = tmp_path / "apps"
    _manifest(apps_dir / "valid", "valid", "Valid App")
    _manifest(apps_dir / "missing-compose", "missing-compose", "Missing Compose", compose=False)
    invalid = apps_dir / "invalid"
    invalid.mkdir()
    (invalid / "meta.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "APPS_DIR", apps_dir)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE app (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, category TEXT, icon_path TEXT, web_ui_port INTEGER, web_ui_path TEXT NOT NULL DEFAULT '/', compose_path TEXT NOT NULL, appdata_path TEXT, enabled INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'managed')"
        )
        conn.execute("INSERT INTO app (id,name,compose_path,enabled,added_at) VALUES ('valid','Old',?,0,'old')", (str(apps_dir / "valid/docker-compose.yml"),))
        conn.commit()

    first = database.ensure_app_catalog()
    second = database.ensure_app_catalog()
    assert first["updated"] == 1
    assert second["created"] == 0
    with database.get_connection() as conn:
        rows = conn.execute("SELECT id, name, enabled FROM app ORDER BY id").fetchall()
    assert [(row[0], row[1], row[2]) for row in rows] == [
        ("missing-compose", "Missing Compose", 1),
        ("valid", "Valid App", 0),
    ]
    response = TestClient(app).get("/api/apps")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["missing-compose"]


def test_overview_bootstrap_degrades_invalid_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "dashboard.db")
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    apps_dir = tmp_path / "apps"
    _manifest(apps_dir / "valid", "valid", "Valid App")
    (apps_dir / "bad").mkdir()
    (apps_dir / "bad/meta.json").write_text(json.dumps({"id": "bad"}), encoding="utf-8")
    monkeypatch.setattr(database, "APPS_DIR", apps_dir)

    response = TestClient(app).get("/api/overview/bootstrap")
    assert response.status_code == 200
    payload = response.json()
    assert payload["apps_summary"]["total"] == 1
    assert payload["apps"][0]["id"] == "valid"
    assert payload["errors"] == []


def test_partial_schema_is_migrated_without_losing_existing_record(monkeypatch, tmp_path):
    db_path = tmp_path / "dashboard.db"
    apps_dir = tmp_path / "apps"
    _manifest(apps_dir / "new", "new", "New App")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "APPS_DIR", apps_dir)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE app (id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, appdata_path TEXT)")
        conn.execute("INSERT INTO app (id, name, appdata_path) VALUES ('legacy', 'Legacy', '/data/legacy')")
        conn.commit()

    assert TestClient(app).get("/api/apps").status_code == 200
    with database.get_connection() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(app)")}
        legacy = conn.execute("SELECT enabled, appdata_path FROM app WHERE id = 'legacy'").fetchone()
        assert {"compose_path", "web_ui_path", "added_at", "source"} <= columns
        assert tuple(legacy) == (0, "/data/legacy")
        assert {row[0] for row in conn.execute("SELECT id FROM app")} == {"legacy", "new"}


def test_ports_check_bootstraps_fresh_database(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "dashboard.db")
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "APPS_DIR", tmp_path / "missing-apps")
    response = TestClient(app, raise_server_exceptions=False).get("/api/apps/ports/check?ports=1234")
    assert response.status_code == 200
    assert response.json()["ports"][0]["known_owner"] is None
