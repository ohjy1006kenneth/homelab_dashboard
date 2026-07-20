from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.database import PROJECT_DIR

router = APIRouter(prefix="/api/overview", tags=["overview"])
CONFIG_PATH = PROJECT_DIR / "dashboard.config.json"
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

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

class GoogleClientSecretPayload(BaseModel):
    client_secret_json: str | dict[str, Any]


class GoogleAuthUrlPayload(BaseModel):
    redirect_uri: str | None = None



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


def _google_paths() -> tuple[Path, Path, Path]:
    hermes_home = Path(os.environ.get("HERMES_HOME", "/home/juyoungoh/.hermes"))
    hermes_home.mkdir(parents=True, exist_ok=True)
    return hermes_home / "google_token.json", hermes_home / "google_client_secret.json", hermes_home / "google_oauth_pending_dashboard.json"


def _token_paths() -> tuple[Path, Path]:
    token_path, client_path, _ = _google_paths()
    return token_path, client_path


def _safe_write_secret(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_client_secret() -> dict[str, Any] | None:
    _, client_path = _token_paths()
    if not client_path.exists():
        return None
    try:
        return json.loads(client_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _client_oauth_config(client_secret: dict[str, Any] | None = None) -> dict[str, Any]:
    data = client_secret or _read_client_secret()
    if not data:
        raise HTTPException(status_code=400, detail="Upload a Google OAuth client JSON first.")
    cfg = data.get("web") or data.get("installed")
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=400, detail="OAuth JSON must contain a web or installed client.")
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        raise HTTPException(status_code=400, detail="OAuth JSON is missing client_id/client_secret.")
    return cfg


def _google_auth_state() -> dict[str, Any]:
    token_path, client_path = _token_paths()
    return {
        "connected": token_path.exists(),
        "setup_required": not token_path.exists(),
        "client_secret_present": client_path.exists(),
        "message": "Google Calendar OAuth is connected." if token_path.exists() else "Connect Google Calendar from this widget.",
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
            "hourly": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "ok": True,
        "location": {"latitude": lat, "longitude": lon, "label": saved.get("label") or "Local weather"},
        "current": data.get("current", {}),
        "hourly": data.get("hourly", {}),
        "daily": data.get("daily", {}),
        "timezone": data.get("timezone"),
    }


@router.post("/google-calendar/client-secret")
def save_google_calendar_client_secret(payload: GoogleClientSecretPayload) -> dict[str, Any]:
    raw = payload.client_secret_json
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Paste the full Google OAuth client JSON file contents.") from exc
    else:
        data = raw
    _client_oauth_config(data)
    _, client_path, _ = _google_paths()
    _safe_write_secret(client_path, data)
    return {"ok": True, "google_calendar": _google_auth_state()}


@router.post("/google-calendar/auth-url")
def google_calendar_auth_url(request: Request, payload: GoogleAuthUrlPayload | None = None) -> dict[str, Any]:
    cfg = _client_oauth_config()
    redirect_uri = (payload.redirect_uri if payload else None) or str(request.url_for("google_calendar_callback"))
    state = secrets.token_urlsafe(24)
    _, _, pending_path = _google_paths()
    _safe_write_secret(pending_path, {"state": state, "redirect_uri": redirect_uri, "created_at": datetime.now(timezone.utc).isoformat()})
    auth_uri = cfg.get("auth_uri") or "https://accounts.google.com/o/oauth2/v2/auth"
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_CALENDAR_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return {"ok": True, "auth_url": f"{auth_uri}?{urlencode(params)}", "redirect_uri": redirect_uri}


@router.get("/google-calendar/callback", name="google_calendar_callback")
def google_calendar_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> HTMLResponse:
    if error:
        return HTMLResponse(f"<h1>Google Calendar not connected</h1><p>{error}</p>", status_code=400)
    if not code or not state:
        return HTMLResponse("<h1>Google Calendar not connected</h1><p>Missing OAuth code/state.</p>", status_code=400)
    token_path, _, pending_path = _google_paths()
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except Exception:
        return HTMLResponse("<h1>Google Calendar not connected</h1><p>OAuth session expired. Reopen Connect Google Calendar from the dashboard.</p>", status_code=400)
    if pending.get("state") != state:
        return HTMLResponse("<h1>Google Calendar not connected</h1><p>OAuth state did not match. Please retry from the dashboard.</p>", status_code=400)
    cfg = _client_oauth_config()
    token_uri = cfg.get("token_uri") or "https://oauth2.googleapis.com/token"
    response = requests.post(
        token_uri,
        data={
            "code": code,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "redirect_uri": pending.get("redirect_uri"),
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if not response.ok:
        detail = response.text[:300]
        return HTMLResponse(f"<h1>Google Calendar not connected</h1><p>Token exchange failed. Check that your OAuth client allows this dashboard callback URL.</p><pre>{detail}</pre>", status_code=400)
    token = response.json()
    if token.get("expires_in"):
        token["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=int(token["expires_in"]))).isoformat().replace("+00:00", "Z")
    token["token"] = token.get("access_token")
    token["client_id"] = cfg["client_id"]
    token["client_secret"] = cfg["client_secret"]
    token["token_uri"] = token_uri
    token["scopes"] = GOOGLE_CALENDAR_SCOPES
    _safe_write_secret(token_path, token)
    try:
        pending_path.unlink()
    except FileNotFoundError:
        pass
    return HTMLResponse("""
<!doctype html><meta charset=\"utf-8\"><title>Google Calendar connected</title>
<style>body{font-family:system-ui;background:#080808;color:#f5f5f5;display:grid;place-items:center;min-height:100vh;margin:0}main{max-width:520px;padding:28px;border:1px solid #333;border-radius:18px;background:#111}button{margin-top:16px;padding:10px 14px;border-radius:10px;border:1px solid #444;background:#fff;color:#000}</style>
<main><h1>Google Calendar connected</h1><p>You can close this tab and return to the dashboard. The Calendar widget will refresh automatically or when you click Refresh.</p><button onclick=\"window.close()\">Close</button><script>try{localStorage.setItem('googleCalendarConnectedAt', String(Date.now()))}catch(e){}; setTimeout(()=>window.close(), 1800)</script></main>
""")


@router.post("/google-calendar/disconnect")
def disconnect_google_calendar() -> dict[str, Any]:
    token_path, _, pending_path = _google_paths()
    for path in (token_path, pending_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return {"ok": True, "google_calendar": _google_auth_state()}


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
