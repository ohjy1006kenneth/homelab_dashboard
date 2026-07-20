from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.database import PROJECT_DIR

router = APIRouter(prefix="/api/overview", tags=["overview"])
CONFIG_PATH = PROJECT_DIR / "dashboard.config.json"

DEFAULT_BOOKMARKS = [
    {"id": "github", "title": "GitHub", "url": "https://github.com/"},
    {"id": "huggingface", "title": "Hugging Face", "url": "https://huggingface.co/"},
    {"id": "youtube", "title": "YouTube", "url": "https://youtube.com/"},
]
DEFAULT_WIDGETS = [
    {"id": "metric-cpu", "type": "metric-cpu", "x": 0, "y": 0, "w": 3, "h": 2},
    {"id": "metric-ram", "type": "metric-ram", "x": 3, "y": 0, "w": 3, "h": 2},
    {"id": "metric-codex", "type": "metric-codex", "x": 6, "y": 0, "w": 3, "h": 2},
    {"id": "clock", "type": "clock", "x": 9, "y": 0, "w": 3, "h": 2},
    {"id": "search", "type": "web-search", "x": 0, "y": 2, "w": 6, "h": 2},
    {"id": "apps", "type": "app-launcher", "x": 0, "y": 4, "w": 8, "h": 4},
    {"id": "webview", "type": "webview", "x": 8, "y": 2, "w": 4, "h": 5},
    {"id": "bookmarks", "type": "bookmarks", "x": 0, "y": 8, "w": 5, "h": 4},
    {"id": "weather", "type": "weather", "x": 5, "y": 8, "w": 3, "h": 3},
    {"id": "calendar", "type": "calendar", "x": 8, "y": 7, "w": 4, "h": 4},
    {"id": "calculator", "type": "calculator", "x": 5, "y": 11, "w": 4, "h": 3},
    {"id": "memo", "type": "memo", "x": 9, "y": 11, "w": 3, "h": 3},
]
DEFAULT_OVERVIEW = {
    "widgets": DEFAULT_WIDGETS,
    "widgets_pinned": False,
    "bookmarks": DEFAULT_BOOKMARKS,
    "webview_urls": {},
    "memos": {},
    "weather": {"latitude": None, "longitude": None, "label": "Local weather"},
}


class OverviewConfig(BaseModel):
    widgets: list[dict[str, Any]] | None = None
    widgets_pinned: bool | None = None
    bookmarks: list[dict[str, Any]] | None = None
    webview_urls: dict[str, str] | None = None
    memos: dict[str, str] | None = None
    weather: dict[str, Any] | None = None


class WeatherLocation(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    label: str | None = None


def _deepcopy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def _overview(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else _read_config()
    overview = _deepcopy(DEFAULT_OVERVIEW)
    stored_raw = cfg.get("overview")
    stored: dict[str, Any] = stored_raw if isinstance(stored_raw, dict) else {}
    for key, value in stored.items():
        if isinstance(value, dict) and isinstance(overview.get(key), dict):
            overview[key].update(value)
        elif value is not None:
            overview[key] = value
    return overview


def _token_paths() -> tuple[Path, Path]:
    hermes_home = Path(os.environ.get("HERMES_HOME", "/home/juyoungoh/.hermes"))
    return hermes_home / "google_token.json", hermes_home / "google_client_secret.json"


def _google_auth_state() -> dict[str, Any]:
    token_path, client_path = _token_paths()
    return {
        "connected": token_path.exists(),
        "setup_required": not token_path.exists(),
        "client_secret_present": client_path.exists(),
        "message": "Google Calendar OAuth is connected." if token_path.exists() else "Google Calendar OAuth is not connected yet.",
    }


def _read_google_token() -> dict[str, Any] | None:
    token_path, _ = _token_paths()
    if not token_path.exists():
        return None
    try:
        token = json.loads(token_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    expiry = token.get("expiry")
    refresh_token = token.get("refresh_token")
    token_uri = token.get("token_uri") or "https://oauth2.googleapis.com/token"
    client_id = token.get("client_id")
    client_secret = token.get("client_secret")
    if expiry and refresh_token and client_id and client_secret:
        try:
            expires_at = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
            if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2):
                response = requests.post(
                    token_uri,
                    data={"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token, "grant_type": "refresh_token"},
                    timeout=10,
                )
                response.raise_for_status()
                refreshed = response.json()
                token["token"] = refreshed.get("access_token", token.get("token"))
                if refreshed.get("expires_in"):
                    token["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=int(refreshed["expires_in"]))).isoformat().replace("+00:00", "Z")
                token_path.write_text(json.dumps(token, indent=2) + "\n", encoding="utf-8")
        except Exception:
            return token
    return token


def _calendar_events(start: datetime, end: datetime) -> list[dict[str, Any]]:
    token = _read_google_token()
    if not token:
        return []
    access_token = token.get("token") or token.get("access_token")
    if not access_token:
        return []
    params = {
        "timeMin": start.isoformat().replace("+00:00", "Z"),
        "timeMax": end.isoformat().replace("+00:00", "Z"),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "50",
    }
    response = requests.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if response.status_code in {401, 403}:
        return []
    response.raise_for_status()
    rows = response.json().get("items", [])
    events = []
    for item in rows:
        events.append({
            "id": item.get("id"),
            "summary": item.get("summary", "Untitled"),
            "start": item.get("start", {}),
            "end": item.get("end", {}),
            "htmlLink": item.get("htmlLink"),
            "location": item.get("location"),
        })
    return events


@router.get("")
def get_overview() -> dict[str, Any]:
    overview = _overview()
    overview["google_calendar"] = _google_auth_state()
    return overview


@router.put("")
def update_overview(payload: OverviewConfig) -> dict[str, Any]:
    cfg = _read_config()
    overview = _overview(cfg)
    data = payload.model_dump(exclude_unset=True)
    if "widgets" in data and data["widgets"] is not None:
        overview["widgets"] = data["widgets"]
    if "widgets_pinned" in data and data["widgets_pinned"] is not None:
        overview["widgets_pinned"] = bool(data["widgets_pinned"])
    if "bookmarks" in data and data["bookmarks"] is not None:
        overview["bookmarks"] = data["bookmarks"]
    if "webview_urls" in data and data["webview_urls"] is not None:
        overview["webview_urls"] = data["webview_urls"]
    if "memos" in data and data["memos"] is not None:
        overview["memos"] = data["memos"]
    if "weather" in data and data["weather"] is not None:
        overview["weather"] = {**overview.get("weather", {}), **data["weather"]}
    cfg["overview"] = overview
    _write_config(cfg)
    return {"ok": True, "overview": get_overview()}


@router.put("/weather/location")
def save_weather_location(payload: WeatherLocation) -> dict[str, Any]:
    cfg = _read_config()
    overview = _overview(cfg)
    overview["weather"] = {"latitude": payload.latitude, "longitude": payload.longitude, "label": payload.label or "Local weather"}
    cfg["overview"] = overview
    _write_config(cfg)
    return {"ok": True, "weather": overview["weather"]}


@router.get("/weather")
def weather(latitude: float | None = Query(default=None, ge=-90, le=90), longitude: float | None = Query(default=None, ge=-180, le=180)) -> dict[str, Any]:
    overview = _overview()
    saved = overview.get("weather", {})
    lat = latitude if latitude is not None else saved.get("latitude")
    lon = longitude if longitude is not None else saved.get("longitude")
    if lat is None or lon is None:
        return {"ok": False, "needs_location": True, "message": "Set or allow a location to show weather."}
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    return {"ok": True, "location": {"latitude": lat, "longitude": lon, "label": saved.get("label") or "Local weather"}, "current": data.get("current", {}), "daily": data.get("daily", {}), "timezone": data.get("timezone")}


@router.get("/calendar")
def calendar(days: int = Query(default=31, ge=1, le=90)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=days)
    google = _google_auth_state()
    events = _calendar_events(start, end) if google["connected"] else []
    return {"ok": True, "google_calendar": google, "events": events, "range": {"start": start.isoformat(), "end": end.isoformat()}}


@router.get("/google-calendar/status")
def google_calendar_status() -> dict[str, Any]:
    return _google_auth_state()
