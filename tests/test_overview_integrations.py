from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import backend.routers.overview as overview
from backend.main import app
from fastapi.testclient import TestClient


GOOGLE_CLIENT_CONFIG = {
    "client_id": "client-id.apps.googleusercontent.com",
    "client_secret": "super-secret",
    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["http://testserver/api/overview/google-calendar/callback"],
}


class FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def make_client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return TestClient(app)


def test_google_calendar_auth_url_validates_origin_and_callback_writes_secretless_token(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    monkeypatch.setattr(overview, "_google_client_config", lambda: GOOGLE_CLIENT_CONFIG)

    rejected = client.post(
        "/api/overview/google-calendar/auth-url",
        json={"redirect_uri": "https://example.com/api/overview/google-calendar/callback"},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "invalid_redirect_uri"

    auth = client.post("/api/overview/google-calendar/auth-url", json={})
    assert auth.status_code == 200
    auth_data = auth.json()
    assert auth_data["ok"] is True
    assert auth_data["redirect_uri"] == "http://testserver/api/overview/google-calendar/callback"

    parsed = urlparse(auth_data["auth_url"])
    state = parse_qs(parsed.query)["state"][0]
    assert parse_qs(parsed.query)["redirect_uri"][0] == auth_data["redirect_uri"]

    def fake_post(url, data=None, timeout=None):
        assert url == GOOGLE_CLIENT_CONFIG["token_uri"]
        assert data["grant_type"] == "authorization_code"
        assert data["client_id"] == GOOGLE_CLIENT_CONFIG["client_id"]
        assert data["client_secret"] == GOOGLE_CLIENT_CONFIG["client_secret"]
        assert data["redirect_uri"] == auth_data["redirect_uri"]
        return FakeResponse(
            {
                "access_token": "ya29.test-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/calendar.readonly",
                "token_type": "Bearer",
            }
        )

    monkeypatch.setattr(overview.requests, "post", fake_post)
    callback = client.get("/api/overview/google-calendar/callback", params={"code": "code-123", "state": state})
    assert callback.status_code == 200
    assert "Google Calendar connected" in callback.text
    assert "window.opener.postMessage" in callback.text
    assert "window.close()" in callback.text

    token_path = overview._google_token_path()
    pending_path = overview._google_pending_path()
    assert not pending_path.exists()
    assert token_path.exists()
    token = json.loads(token_path.read_text(encoding="utf-8"))
    assert token["access_token"] == "ya29.test-token"
    assert token["refresh_token"] == "refresh-token"
    assert "client_secret" not in token
    assert "client_id" not in token
    assert token_path.stat().st_mode & 0o777 == 0o600

    expired = client.get("/api/overview/google-calendar/callback", params={"code": "code-123", "state": state})
    assert expired.status_code == 400
    assert "expired" in expired.text.lower() or "state did not match" in expired.text.lower()


def test_calendar_states_and_stale_fallback(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    monkeypatch.setattr(overview, "_google_client_config", lambda: None)
    setup_required = client.get("/api/overview/calendar")
    assert setup_required.status_code == 200
    setup_data = setup_required.json()
    assert setup_data["ok"] is False
    assert setup_data["google_calendar"]["status"] == "setup_required"
    assert setup_data["events"] == []

    monkeypatch.setattr(overview, "_google_client_config", lambda: GOOGLE_CLIENT_CONFIG)
    configured = client.get("/api/overview/calendar")
    assert configured.status_code == 200
    configured_data = configured.json()
    assert configured_data["ok"] is True
    assert configured_data["google_calendar"]["status"] == "configured"
    assert configured_data["events"] == []

    token_payload = {
        "access_token": "ya29.calendar-token",
        "refresh_token": "refresh-calendar-token",
        "expiry": "2099-01-01T00:00:00Z",
        "token_uri": GOOGLE_CLIENT_CONFIG["token_uri"],
    }
    overview._safe_write_secret(overview._google_token_path(), token_payload)

    calendar_payload = {
        "items": [
            {
                "id": "evt-1",
                "summary": "Standup",
                "htmlLink": "https://calendar.google.com/event?eid=evt-1",
                "location": "Zoom",
                "start": {"dateTime": "2026-07-21T09:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-07-21T09:30:00Z", "timeZone": "UTC"},
            }
        ]
    }

    def fake_calendar_get(url, params=None, headers=None, timeout=None):
        assert url.endswith("/calendar/v3/calendars/primary/events")
        assert headers["Authorization"] == "Bearer ya29.calendar-token"
        return FakeResponse(calendar_payload)

    monkeypatch.setattr(overview.requests, "get", fake_calendar_get)
    connected = client.get("/api/overview/calendar")
    assert connected.status_code == 200
    connected_data = connected.json()
    assert connected_data["ok"] is True
    assert connected_data["google_calendar"]["status"] == "connected"
    assert connected_data["cache"]["hit"] is False
    assert connected_data["events"][0]["start_at"] == "2026-07-21T09:00:00Z"
    assert connected_data["events"][0]["end_at"] == "2026-07-21T09:30:00Z"
    assert connected_data["events"][0]["location"] == "Zoom"
    assert connected_data["events"][0]["all_day"] is False

    monkeypatch.setattr(overview.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    stale = client.get("/api/overview/calendar?refresh=true")
    assert stale.status_code == 200
    stale_data = stale.json()
    assert stale_data["ok"] is True
    assert stale_data["stale"] is True
    assert stale_data["events"][0]["summary"] == "Standup"
    assert "refresh failed" in stale_data["message"].lower()


def test_weather_states_cache_and_stale_fallback(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    missing = client.get("/api/overview/weather")
    assert missing.status_code == 200
    missing_data = missing.json()
    assert missing_data["ok"] is False
    assert missing_data["status"] == "needs_location"
    assert missing_data["location"] is None

    weather_payload = {
        "timezone": "UTC",
        "current": {
            "time": "2026-07-21T15:00",
            "temperature_2m": 27.2,
            "relative_humidity_2m": 58,
            "wind_speed_10m": 11.3,
            "weather_code": 2,
            "is_day": 1,
        },
        "current_units": {
            "temperature_2m": "°C",
            "relative_humidity_2m": "%",
            "wind_speed_10m": "km/h",
        },
        "hourly": {
            "time": ["2026-07-21T15:00", "2026-07-21T16:00", "2026-07-21T17:00"],
            "temperature_2m": [27.2, 26.8, 25.4],
            "precipitation_probability": [10, 20, 40],
            "weather_code": [2, 61, 61],
            "wind_speed_10m": [11.3, 13.1, 12.5],
        },
        "hourly_units": {
            "temperature_2m": "°C",
            "precipitation_probability": "%",
            "wind_speed_10m": "km/h",
        },
        "daily": {
            "temperature_2m_max": [28.0],
            "temperature_2m_min": [22.0],
        },
        "daily_units": {
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
        },
    }

    calls = {"count": 0}

    def fake_weather_get(url, params=None, timeout=None):
        calls["count"] += 1
        assert url == "https://api.open-meteo.com/v1/forecast"
        assert params["latitude"] == 12.345
        assert params["longitude"] == 67.89
        return FakeResponse(weather_payload)

    monkeypatch.setattr(overview.requests, "get", fake_weather_get)
    first = client.get("/api/overview/weather?latitude=12.345&longitude=67.89")
    assert first.status_code == 200
    first_data = first.json()
    assert calls["count"] == 1
    assert first_data["ok"] is True
    assert first_data["provider"] == "open-meteo"
    assert first_data["cache"]["hit"] is False
    assert first_data["current"]["temperature"] == 27.2
    assert first_data["current"]["condition"] == "Partly cloudy"
    assert first_data["hourly"][0]["timestamp"] == "2026-07-21T15:00"
    assert first_data["hourly"][1]["condition"] == "Rain"
    assert first_data["daily"]["temperature_2m_max"] == [28.0]

    monkeypatch.setattr(overview.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network should not be used")))
    cached = client.get("/api/overview/weather?latitude=12.345&longitude=67.89")
    assert cached.status_code == 200
    cached_data = cached.json()
    assert cached_data["cache"]["hit"] is True
    assert cached_data["current"]["temperature"] == 27.2

    stale = client.get("/api/overview/weather?latitude=12.345&longitude=67.89&refresh=true")
    assert stale.status_code == 200
    stale_data = stale.json()
    assert stale_data["ok"] is True
    assert stale_data["stale"] is True
    assert stale_data["current"]["condition"] == "Partly cloudy"
    assert "weather provider unavailable" in stale_data["error"].lower()


def test_openapi_excludes_google_calendar_client_secret_route():
    client = TestClient(app)
    schema = client.get("/openapi.json").json()
    assert "/api/overview/google-calendar/auth-url" in schema["paths"]
    assert "/api/overview/google-calendar/client-secret" not in schema["paths"]
