from __future__ import annotations

import json

from fastapi.testclient import TestClient

import backend.routers.settings as settings
from backend.main import app


def test_settings_round_trip_pinned_app_ids(monkeypatch, tmp_path):
    config_path = tmp_path / "dashboard.config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", config_path)
    client = TestClient(app)

    assert client.get("/api/settings").json()["pinned_app_ids"] == []
    response = client.put("/api/settings", json={"pinned_app_ids": [" valid ", "", "stale"]})
    assert response.status_code == 200
    assert response.json()["settings"]["pinned_app_ids"] == ["valid", "stale"]
    assert json.loads(config_path.read_text())["pinned_app_ids"] == ["valid", "stale"]