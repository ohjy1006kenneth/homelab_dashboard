from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.database import PROJECT_DIR
from backend.routers import apps as apps_router
from backend.routers.metrics import current_metrics

router = APIRouter(prefix="/api/overview", tags=["overview"])
CONFIG_PATH = PROJECT_DIR / "dashboard.config.json"
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
GOOGLE_OAUTH_STATE_TTL_SECONDS = 10 * 60
WEATHER_FRESH_TTL_SECONDS = 10 * 60
WEATHER_STALE_TTL_SECONDS = 24 * 60 * 60
CALENDAR_FRESH_TTL_SECONDS = 5 * 60
CALENDAR_STALE_TTL_SECONDS = 24 * 60 * 60
OVERVIEW_CACHE: dict[str, dict[str, Any]] = {}
WEATHER_FETCH_LOCK = threading.Lock()
CALENDAR_FETCH_LOCK = threading.Lock()

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


def _cache_response(value: dict[str, Any], *, hit: bool, age_seconds: float, ttl_seconds: int | None, stale_ttl_seconds: int | None) -> dict[str, Any]:
    response = _deepcopy(value)
    if isinstance(response, dict):
        response["cache"] = {
            "hit": hit,
            "age_seconds": round(age_seconds, 1),
            "ttl_seconds": ttl_seconds,
            "stale_ttl_seconds": stale_ttl_seconds,
        }
    return response


def _cache_set(key: str, value: dict[str, Any]) -> None:
    OVERVIEW_CACHE[key] = {"at": time.time(), "value": _deepcopy(value)}


def _cache_get(
    key: str,
    fresh_ttl_seconds: int,
    stale_ttl_seconds: int | None = None,
    *,
    allow_stale: bool = True,
) -> dict[str, Any] | None:
    row = OVERVIEW_CACHE.get(key)
    if not row:
        return None
    age_seconds = time.time() - float(row.get("at", 0))
    if age_seconds <= fresh_ttl_seconds:
        return _cache_response(row["value"], hit=True, age_seconds=age_seconds, ttl_seconds=fresh_ttl_seconds, stale_ttl_seconds=stale_ttl_seconds)
    if allow_stale and stale_ttl_seconds is not None and age_seconds <= stale_ttl_seconds:
        response = _cache_response(row["value"], hit=False, age_seconds=age_seconds, ttl_seconds=fresh_ttl_seconds, stale_ttl_seconds=stale_ttl_seconds)
        response["stale"] = True
        return response
    return None


def _safe_call(name: str, fn, fallback: Any) -> dict[str, Any]:
    try:
        return {"ok": True, "data": fn(), "error": None}
    except Exception as exc:  # noqa: BLE001 - overview bootstrap should degrade widget-by-widget
        return {"ok": False, "data": fallback, "error": f"{name}: {str(exc)[:180]}"}


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


def _google_home() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME", "/home/juyoungoh/.hermes")).expanduser()
    hermes_home.mkdir(parents=True, exist_ok=True)
    return hermes_home


def _google_token_path() -> Path:
    return _google_home() / "google_token.json"


def _google_client_secret_path() -> Path:
    return _google_home() / "google_client_secret.json"


def _google_pending_path() -> Path:
    return _google_home() / "google_oauth_pending_dashboard.json"


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _safe_write_secret(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _normalize_google_client_config(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    cfg = data.get("web") if isinstance(data.get("web"), dict) else data.get("installed") if isinstance(data.get("installed"), dict) else data
    if not isinstance(cfg, dict):
        return None
    client_id = cfg.get("client_id") or data.get("client_id")
    client_secret = cfg.get("client_secret") or data.get("client_secret")
    if not client_id or not client_secret:
        return None
    auth_uri = cfg.get("auth_uri") or data.get("auth_uri") or "https://accounts.google.com/o/oauth2/v2/auth"
    token_uri = cfg.get("token_uri") or data.get("token_uri") or "https://oauth2.googleapis.com/token"
    redirect_uris = cfg.get("redirect_uris") or data.get("redirect_uris") or []
    if isinstance(redirect_uris, str):
        redirect_uris = [redirect_uris]
    redirect_uris = [str(uri) for uri in redirect_uris if uri]
    return {
        "client_id": str(client_id),
        "client_secret": str(client_secret),
        "auth_uri": str(auth_uri),
        "token_uri": str(token_uri),
        "redirect_uris": redirect_uris,
    }


def _google_client_config() -> dict[str, Any] | None:
    env_json = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
    if env_json:
        try:
            return _normalize_google_client_config(json.loads(env_json))
        except json.JSONDecodeError:
            return None
    env_file = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_FILE")
    if env_file:
        try:
            return _normalize_google_client_config(json.loads(Path(env_file).expanduser().read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None
    default_path = _google_client_secret_path()
    file_config = _read_json_file(default_path)
    if file_config:
        return _normalize_google_client_config(file_config)
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if client_id and client_secret:
        redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
        return _normalize_google_client_config({
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": os.environ.get("GOOGLE_OAUTH_AUTH_URI") or "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": os.environ.get("GOOGLE_OAUTH_TOKEN_URI") or "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri] if redirect_uri else [],
        })
    return None


def _google_auth_state() -> dict[str, Any]:
    configured = _google_client_config() is not None
    token = _read_json_file(_google_token_path())
    connected = bool(token and (token.get("access_token") or token.get("token") or token.get("refresh_token")))
    if connected:
        status = "connected"
        message = "Google Calendar is connected."
    elif configured:
        status = "configured"
        message = "Connect Google Calendar to show events."
    else:
        status = "setup_required"
        message = "Google Calendar server OAuth is not configured."
    return {
        "status": status,
        "configured": configured,
        "connected": connected,
        "setup_required": not configured,
        "message": message,
    }


def _refresh_google_token(token: dict[str, Any]) -> dict[str, Any]:
    cfg = _google_client_config()
    refresh_token = token.get("refresh_token")
    if not cfg or not refresh_token:
        return token
    token_uri = cfg.get("token_uri") or "https://oauth2.googleapis.com/token"
    response = requests.post(
        token_uri,
        data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    response.raise_for_status()
    refreshed = response.json()
    updated = dict(token)
    if refreshed.get("access_token"):
        updated["access_token"] = refreshed["access_token"]
    if refreshed.get("expires_in"):
        updated["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=int(refreshed["expires_in"]))).isoformat().replace("+00:00", "Z")
    if refreshed.get("refresh_token"):
        updated["refresh_token"] = refreshed["refresh_token"]
    updated.pop("client_id", None)
    updated.pop("client_secret", None)
    updated["token_uri"] = token_uri
    _safe_write_secret(_google_token_path(), updated)
    return updated


def _read_google_token() -> dict[str, Any] | None:
    token = _read_json_file(_google_token_path())
    if not token:
        return None
    expiry = token.get("expiry")
    refresh_token = token.get("refresh_token")
    if expiry and refresh_token:
        try:
            expires_at = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
            if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2):
                token = _refresh_google_token(token)
        except Exception:  # noqa: BLE001 - if refresh fails, keep the last good token until it expires
            return token
    return token


def _clear_google_pending_state() -> None:
    try:
        _google_pending_path().unlink()
    except FileNotFoundError:
        pass


def _origin_from_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _calendar_callback_error(message: str, status_code: int = 400) -> HTMLResponse:
    safe_message = html_escape(message)
    return HTMLResponse(
        f"""<!doctype html>
<html lang=\"en\"><meta charset=\"utf-8\"><title>Google Calendar not connected</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #080808; color: #f5f5f5; display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  main {{ max-width: 560px; padding: 28px; border: 1px solid #333; border-radius: 18px; background: #111; }}
  pre {{ white-space: pre-wrap; word-break: break-word; color: #cfcfcf; background: #090909; border: 1px solid #222; border-radius: 12px; padding: 12px; }}
  button {{ margin-top: 16px; padding: 10px 14px; border-radius: 10px; border: 1px solid #444; background: #fff; color: #000; }}
</style>
<main>
  <h1>Google Calendar not connected</h1>
  <p>{safe_message}</p>
  <button onclick=\"window.close()\">Close</button>
</main>
</html>""",
        status_code=status_code,
    )


def _calendar_callback_success() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang=\"en\"><meta charset=\"utf-8\"><title>Google Calendar connected</title>
<style>
  body { font-family: system-ui, sans-serif; background: #080808; color: #f5f5f5; display: grid; place-items: center; min-height: 100vh; margin: 0; }
  main { max-width: 560px; padding: 28px; border: 1px solid #333; border-radius: 18px; background: #111; }
  button { margin-top: 16px; padding: 10px 14px; border-radius: 10px; border: 1px solid #444; background: #fff; color: #000; }
</style>
<main>
  <h1>Google Calendar connected</h1>
  <p>You can close this tab and return to the dashboard. The Calendar widget will refresh automatically.</p>
  <button onclick=\"window.close()\">Close</button>
  <script>
    try {
      if (window.opener && !window.opener.closed) {
        window.opener.postMessage({ type: 'overview-google-calendar-connected' }, location.origin);
      }
      localStorage.setItem('googleCalendarConnectedAt', String(Date.now()));
    } catch (error) {}
    setTimeout(() => window.close(), 1200);
  </script>
</main>
</html>"""
    )


def _validate_google_callback_origin(request: Request, redirect_uri: str | None) -> str:
    callback_url = str(request.url_for("google_calendar_callback"))
    expected_origin = _origin_from_url(callback_url)
    request_origin = _origin_from_url(str(request.base_url)) or expected_origin
    if redirect_uri:
        parsed = urlparse(redirect_uri)
        if _origin_from_url(redirect_uri) != request_origin or parsed.path != urlparse(callback_url).path:
            raise HTTPException(status_code=400, detail={"code": "invalid_redirect_uri", "message": "Google Calendar redirect URI must use the dashboard callback origin."})
    return callback_url


def _normalize_calendar_event(item: dict[str, Any]) -> dict[str, Any]:
    start = item.get("start") if isinstance(item.get("start"), dict) else {}
    end = item.get("end") if isinstance(item.get("end"), dict) else {}
    start_at = start.get("dateTime") or start.get("date")
    end_at = end.get("dateTime") or end.get("date")
    return {
        "id": item.get("id"),
        "summary": item.get("summary", "Untitled"),
        "html_link": item.get("htmlLink"),
        "location": item.get("location"),
        "status": item.get("status"),
        "start": start,
        "end": end,
        "start_at": start_at,
        "end_at": end_at,
        "all_day": bool(start.get("date") and not start.get("dateTime")),
        "timezone": start.get("timeZone") or end.get("timeZone"),
    }


def _calendar_event_window(start: datetime, end: datetime, days: int, connected: bool, status: dict[str, Any], events: list[dict[str, Any]], error: str | None, stale: bool, fetched_at: datetime) -> dict[str, Any]:
    google_state = {
        **status,
        "message": status.get("message") or ("Google Calendar is connected." if connected else "Connect Google Calendar to show events."),
    }
    ok = status.get("status") != "setup_required" and error is None
    if stale:
        ok = True
    return {
        "ok": ok,
        "provider": "google-calendar",
        "status": "stale" if stale else (status.get("status") or "configured"),
        "stale": stale,
        "fetched_at": fetched_at.isoformat(),
        "google_calendar": google_state,
        "events": events,
        "error": error,
        "message": error or google_state.get("message"),
        "range": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
    }


def _calendar_events(start: datetime, end: datetime) -> list[dict[str, Any]]:
    token = _read_google_token()
    if not token:
        return []
    access_token = token.get("access_token") or token.get("token")
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
    response.raise_for_status()
    rows = response.json().get("items", [])
    events: list[dict[str, Any]] = []
    for item in rows:
        if isinstance(item, dict):
            events.append(_normalize_calendar_event(item))
    return events


def _weather_condition_label(code: Any) -> str:
    value = None
    try:
        value = int(float(code))
    except Exception:  # noqa: BLE001 - weather codes may be missing/partial
        value = None
    if value == 0:
        return "Clear"
    if value in {1, 2}:
        return "Partly cloudy"
    if value == 3:
        return "Cloudy"
    if value in {45, 48}:
        return "Fog"
    if value in {51, 53, 55, 56, 57}:
        return "Drizzle"
    if value in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "Rain"
    if value in {71, 73, 75, 77, 85, 86}:
        return "Snow"
    if value in {95, 96, 99}:
        return "Storm"
    return "Weather"


def _weather_units(data: dict[str, Any]) -> dict[str, Any]:
    current_units = data.get("current_units") if isinstance(data.get("current_units"), dict) else {}
    hourly_units = data.get("hourly_units") if isinstance(data.get("hourly_units"), dict) else {}
    daily_units = data.get("daily_units") if isinstance(data.get("daily_units"), dict) else {}
    return {
        "temperature": current_units.get("temperature_2m") or hourly_units.get("temperature_2m") or "°C",
        "humidity": current_units.get("relative_humidity_2m") or "%",
        "precipitation_probability": hourly_units.get("precipitation_probability") or "%",
        "wind_speed": current_units.get("wind_speed_10m") or hourly_units.get("wind_speed_10m") or "km/h",
        "daily_temperature": daily_units.get("temperature_2m_max") or "°C",
    }


def _normalize_weather_current(current: dict[str, Any], fetched_at: datetime, units: dict[str, Any]) -> dict[str, Any]:
    temperature = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    wind_speed = current.get("wind_speed_10m")
    weather_code = current.get("weather_code")
    timestamp = current.get("time") or fetched_at.isoformat()
    return {
        "timestamp": timestamp,
        "time": timestamp,
        "temperature": temperature,
        "temperature_2m": temperature,
        "humidity": humidity,
        "relative_humidity_2m": humidity,
        "wind_speed": wind_speed,
        "wind_speed_10m": wind_speed,
        "weather_code": weather_code,
        "condition": _weather_condition_label(weather_code),
        "is_day": current.get("is_day"),
        "units": units,
    }


def _normalize_weather_hourly(hourly: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(hourly, dict):
        return []
    times = list(hourly.get("time") or [])
    temps = list(hourly.get("temperature_2m") or [])
    precipitation = list(hourly.get("precipitation_probability") or [])
    wind_speeds = list(hourly.get("wind_speed_10m") or [])
    codes = list(hourly.get("weather_code") or [])
    max_len = max(len(times), len(temps), len(precipitation), len(wind_speeds), len(codes))
    records: list[dict[str, Any]] = []
    for index in range(max_len):
        timestamp = times[index] if index < len(times) else None
        if not timestamp:
            continue
        records.append({
            "timestamp": timestamp,
            "time": timestamp,
            "temperature": temps[index] if index < len(temps) else None,
            "temperature_2m": temps[index] if index < len(temps) else None,
            "precipitation_probability": precipitation[index] if index < len(precipitation) else None,
            "wind_speed": wind_speeds[index] if index < len(wind_speeds) else None,
            "wind_speed_10m": wind_speeds[index] if index < len(wind_speeds) else None,
            "weather_code": codes[index] if index < len(codes) else None,
            "condition": _weather_condition_label(codes[index] if index < len(codes) else None),
        })
    return records


def _build_weather_response(data: dict[str, Any], *, latitude: float, longitude: float, label: str | None) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc)
    current_raw = data.get("current") if isinstance(data.get("current"), dict) else {}
    hourly_raw = data.get("hourly") if isinstance(data.get("hourly"), dict) else {}
    daily_raw = data.get("daily") if isinstance(data.get("daily"), dict) else {}
    units = _weather_units(data)
    current = _normalize_weather_current(current_raw, fetched_at, units)
    hourly = _normalize_weather_hourly(hourly_raw)
    current_units = data.get("current_units") if isinstance(data.get("current_units"), dict) else {}
    hourly_units = data.get("hourly_units") if isinstance(data.get("hourly_units"), dict) else {}
    daily_units = data.get("daily_units") if isinstance(data.get("daily_units"), dict) else {}
    return {
        "ok": True,
        "provider": "open-meteo",
        "status": "live",
        "stale": False,
        "fetched_at": fetched_at.isoformat(),
        "location": {"latitude": latitude, "longitude": longitude, "label": label or "Local weather"},
        "timezone": data.get("timezone"),
        "units": units,
        "current_units": current_units,
        "hourly_units": hourly_units,
        "daily_units": daily_units,
        "current": current,
        "hourly": hourly,
        "daily": daily_raw,
        "current_raw": current_raw,
        "hourly_raw": hourly_raw,
        "daily_raw": daily_raw,
    }


def _overview_status_payload(request: Request | None = None) -> dict[str, Any]:
    app_result = _safe_call("apps", lambda: apps_router.list_apps(request, health=True), [])
    app_rows = app_result["data"] or []
    app_summary = {
        "total": len(app_rows),
        "up": sum(1 for app in app_rows if app.get("status") == "running" and (app.get("health") or {}).get("ok") is not False),
        "web_down": sum(1 for app in app_rows if app.get("status") == "running" and (app.get("health") or {}).get("ok") is False),
        "down": sum(1 for app in app_rows if app.get("status") in {"stopped", "missing"}),
        "unknown": sum(1 for app in app_rows if app.get("status") not in {"running", "stopped", "missing"}),
    }
    metric_result = _safe_call("metrics", current_metrics, {})
    errors = [row["error"] for row in (app_result, metric_result) if row.get("error")]
    return {
        "ok": not errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "apps_summary": app_summary,
        "apps": app_rows,
        "metrics": metric_result["data"],
        "google_calendar": _google_auth_state(),
        "errors": errors,
    }


@router.get("/status")
def overview_status(request: Request) -> dict[str, Any]:
    return _overview_status_payload(request)


@router.get("/bootstrap")
def overview_bootstrap(request: Request) -> dict[str, Any]:
    overview = get_overview()
    status = _overview_status_payload(request)
    return {"ok": True, "overview": overview, **status}


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
def weather(
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    refresh: bool = False,
) -> dict[str, Any]:
    overview = _overview()
    saved = overview.get("weather", {})
    lat = latitude if latitude is not None else saved.get("latitude")
    lon = longitude if longitude is not None else saved.get("longitude")
    if lat is None or lon is None:
        return {
            "ok": False,
            "provider": "open-meteo",
            "status": "needs_location",
            "stale": False,
            "message": "Set or allow a location to show weather.",
            "error": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "location": None,
            "hourly": [],
            "current": None,
            "daily": {},
        }
    cache_key = f"weather:{round(float(lat), 3)}:{round(float(lon), 3)}"
    if not refresh:
        cached = _cache_get(cache_key, WEATHER_FRESH_TTL_SECONDS, WEATHER_STALE_TTL_SECONDS, allow_stale=False)
        if cached:
            return cached
    with WEATHER_FETCH_LOCK:
        if not refresh:
            cached = _cache_get(cache_key, WEATHER_FRESH_TTL_SECONDS, WEATHER_STALE_TTL_SECONDS, allow_stale=False)
            if cached:
                return cached
        try:
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
            result = _build_weather_response(data, latitude=float(lat), longitude=float(lon), label=saved.get("label") or "Local weather")
            _cache_set(cache_key, result)
            return _cache_response(result, hit=False, age_seconds=0, ttl_seconds=WEATHER_FRESH_TTL_SECONDS, stale_ttl_seconds=WEATHER_STALE_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 - fallback to last good weather if available
            cached = _cache_get(cache_key, WEATHER_FRESH_TTL_SECONDS, WEATHER_STALE_TTL_SECONDS, allow_stale=True)
            message = f"Weather provider unavailable: {str(exc)[:160]}"
            if cached:
                cached["ok"] = True
                cached["stale"] = True
                cached["status"] = "stale"
                cached["error"] = message
                cached["message"] = f"Showing cached weather because refresh failed: {str(exc)[:120]}"
                return cached
            return {
                "ok": False,
                "provider": "open-meteo",
                "status": "error",
                "stale": False,
                "message": message,
                "error": message,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "location": {"latitude": float(lat), "longitude": float(lon), "label": saved.get("label") or "Local weather"},
                "hourly": [],
                "current": None,
                "daily": {},
            }


@router.post("/google-calendar/auth-url")
def google_calendar_auth_url(request: Request, payload: GoogleAuthUrlPayload | None = None) -> dict[str, Any]:
    cfg = _google_client_config()
    if not cfg:
        raise HTTPException(status_code=400, detail={"code": "google_calendar_setup_required", "message": "Google Calendar server OAuth is not configured."})
    callback_url = _validate_google_callback_origin(request, payload.redirect_uri if payload else None)
    state = secrets.token_urlsafe(24)
    pending_path = _google_pending_path()
    _safe_write_secret(
        pending_path,
        {
            "state": state,
            "redirect_uri": callback_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=GOOGLE_OAUTH_STATE_TTL_SECONDS)).isoformat().replace("+00:00", "Z"),
        },
    )
    auth_uri = cfg.get("auth_uri") or "https://accounts.google.com/o/oauth2/v2/auth"
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": " ".join(GOOGLE_CALENDAR_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return {"ok": True, "auth_url": f"{auth_uri}?{urlencode(params)}", "redirect_uri": callback_url, "state_expires_in": GOOGLE_OAUTH_STATE_TTL_SECONDS}


@router.get("/google-calendar/callback", name="google_calendar_callback")
def google_calendar_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> HTMLResponse:
    pending_path = _google_pending_path()
    pending = _read_json_file(pending_path)
    if error:
        _clear_google_pending_state()
        return _calendar_callback_error(f"Google returned an authorization error: {error}")
    if not code or not state:
        _clear_google_pending_state()
        return _calendar_callback_error("Missing OAuth code/state.")
    if not pending:
        _clear_google_pending_state()
        return _calendar_callback_error("OAuth session expired. Reopen Connect Google Calendar from the dashboard.")
    expires_at_raw = pending.get("expires_at")
    try:
        expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00")) if expires_at_raw else None
    except Exception:  # noqa: BLE001
        expires_at = None
    if expires_at and expires_at <= datetime.now(timezone.utc):
        _clear_google_pending_state()
        return _calendar_callback_error("OAuth session expired. Reopen Connect Google Calendar from the dashboard.")
    if pending.get("state") != state:
        _clear_google_pending_state()
        return _calendar_callback_error("OAuth state did not match. Please retry from the dashboard.")
    cfg = _google_client_config()
    if not cfg:
        _clear_google_pending_state()
        return _calendar_callback_error("Google Calendar server OAuth is not configured.")
    token_uri = cfg.get("token_uri") or "https://oauth2.googleapis.com/token"
    try:
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
            _clear_google_pending_state()
            return _calendar_callback_error(
                f"Token exchange failed. Check that your OAuth client allows this dashboard callback URL. Details: {detail}",
                status_code=400,
            )
        token = response.json()
        if token.get("expires_in"):
            token["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=int(token["expires_in"]))).isoformat().replace("+00:00", "Z")
        token["access_token"] = token.get("access_token") or token.get("token")
        token["token_uri"] = token_uri
        token.pop("client_id", None)
        token.pop("client_secret", None)
        _safe_write_secret(_google_token_path(), token)
        _clear_google_pending_state()
        return _calendar_callback_success()
    except Exception as exc:  # noqa: BLE001 - sanitized to avoid exposing secrets or traces
        _clear_google_pending_state()
        return _calendar_callback_error(f"Google Calendar authorization failed: {str(exc)[:240]}")


@router.post("/google-calendar/disconnect")
def disconnect_google_calendar() -> dict[str, Any]:
    for path in (_google_token_path(), _google_pending_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return {"ok": True, "google_calendar": _google_auth_state()}


@router.get("/calendar")
def calendar(days: int = Query(default=31, ge=1, le=90), refresh: bool = False) -> dict[str, Any]:
    google = _google_auth_state()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=days)
    cache_key = f"calendar:{days}:{google.get('connected')}"
    if google["connected"] and not refresh:
        cached = _cache_get(cache_key, CALENDAR_FRESH_TTL_SECONDS, CALENDAR_STALE_TTL_SECONDS, allow_stale=False)
        if cached:
            return cached
    if not google["configured"] and not google["connected"]:
        return _calendar_event_window(start, end, days, False, google, [], None, False, now)
    with CALENDAR_FETCH_LOCK:
        if google["connected"] and not refresh:
            cached = _cache_get(cache_key, CALENDAR_FRESH_TTL_SECONDS, CALENDAR_STALE_TTL_SECONDS, allow_stale=False)
            if cached:
                return cached
        events: list[dict[str, Any]] = []
        error: str | None = None
        error_detail = ""
        stale = False
        try:
            events = _calendar_events(start, end) if google["connected"] else []
        except Exception as exc:  # noqa: BLE001 - fallback to last good events if available
            error_detail = str(exc)[:180]
            error = f"Calendar provider unavailable: {error_detail}"
            stale = True
        result = _calendar_event_window(start, end, days, google["connected"], google, events, error, False, now)
        if error is None:
            _cache_set(cache_key, result)
            return _cache_response(result, hit=False, age_seconds=0, ttl_seconds=CALENDAR_FRESH_TTL_SECONDS, stale_ttl_seconds=CALENDAR_STALE_TTL_SECONDS)
        cached = _cache_get(cache_key, CALENDAR_FRESH_TTL_SECONDS, CALENDAR_STALE_TTL_SECONDS, allow_stale=True)
        if cached:
            cached["ok"] = True
            cached["stale"] = True
            cached["status"] = "stale"
            cached["error"] = error
            cached["message"] = f"Showing cached calendar events because refresh failed: {error_detail}"
            return cached
        result["ok"] = False
        result["stale"] = False
        result["status"] = "setup_required" if not google["configured"] else "error"
        result["error"] = error
        result["message"] = error
        return result


@router.get("/google-calendar/status")
def google_calendar_status() -> dict[str, Any]:
    return _google_auth_state()
