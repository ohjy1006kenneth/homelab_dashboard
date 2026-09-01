#!/usr/bin/env python3
"""Silent watchdog for the canonical DashCraft dashboard open links.

Prints only when something looks broken so no_agent cron stays quiet when healthy.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DASHBOARD = "http://127.0.0.1:8765"
LAN_HOST = "192.168.1.29:8765"
PROJECT_DIR = Path(__file__).resolve().parent.parent
USER_SERVICE = "lab-dashboard.service"
START_CMD = ["systemctl", "--user", "start", USER_SERVICE]


def run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)


def fetch_json(url: str, host: str | None = None):
    req = urllib.request.Request(url)
    if host:
        req.add_header("Host", host)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8", errors="replace")


def is_dashboard_checkout(path: Path) -> bool:
    return (path / "frontend" / "app.js").is_file() and (path / "backend" / "main.py").is_file()


def resolve_project_dir() -> Path | None:
    """Choose the script checkout, falling back only to a valid cron workdir."""
    for candidate in (PROJECT_DIR, Path.cwd()):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if is_dashboard_checkout(resolved):
            return resolved
    return None


def parse_redirect_response(output: str) -> tuple[str | None, str | None]:
    """Return the first response status and exact Location header value."""
    lines = output.splitlines()
    status = next((line.split(None, 2)[1] for line in lines if line.startswith("HTTP/") and len(line.split()) >= 2), None)
    location = next(
        (line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("location:")),
        None,
    )
    return status, location


def start_dashboard_if_needed(problems: list[str]) -> bool:
    start = run(START_CMD, timeout=15)
    if start.returncode != 0:
        problems.append(f"systemd user start failed: {start.stderr.strip() or start.stdout.strip()}")
        return False
    for _ in range(15):
        try:
            if fetch_json(f"{DASHBOARD}/api/health") == {"ok": True}:
                return True
        except Exception:
            time.sleep(1)
    return False


def main() -> int:
    problems: list[str] = []
    project_dir = resolve_project_dir()
    if project_dir is None:
        problems.append(
            "dashboard project root not found; run from a checkout containing "
            "frontend/app.js and backend/main.py"
        )

    # Keep the service probe as a quiet diagnostic; the HTTP health probe below
    # is authoritative and performs the existing start/retry behavior when down.
    run(["systemctl", "is-active", USER_SERVICE])

    try:
        health = fetch_json(f"{DASHBOARD}/api/health")
        if health != {"ok": True}:
            problems.append(f"/api/health returned {health!r}")
    except Exception as exc:  # noqa: BLE001 - watchdog report
        if start_dashboard_if_needed(problems):
            health = {"ok": True}
        else:
            problems.append(f"/api/health failed after restart attempt: {exc}")

    # /api/health can be green while the browser is stuck on the static loading
    # shell because a top-level ES-module error prevents frontend load().
    try:
        index_html = fetch_text(f"{DASHBOARD}/")
        script_match = re.search(r'<script[^>]+src="([^"]*app\.js[^"]*)"', index_html)
        if not script_match:
            problems.append("index.html does not reference /static/app.js")
        if "Loading dashboard" not in index_html:
            problems.append("index.html static loading shell is missing/unexpected")

        if project_dir is not None:
            with (project_dir / "frontend" / "app.js").open("rb") as app_module:
                module_check = subprocess.run(
                    ["node", "--input-type=module", "--check"],
                    stdin=app_module,
                    text=True,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            if module_check.returncode != 0:
                problems.append(
                    "frontend/app.js fails browser-module syntax check: "
                    + (module_check.stderr or module_check.stdout).strip()
                )
    except Exception as exc:  # noqa: BLE001 - watchdog report
        problems.append(f"frontend static/module check failed: {exc}")

    try:
        apps = fetch_json(f"{DASHBOARD}/api/apps", host=LAN_HOST)
    except Exception as exc:  # noqa: BLE001 - watchdog report
        apps = []
        problems.append(f"/api/apps failed: {exc}")

    for app in apps:
        port = app.get("web_ui_port")
        if not port:
            continue
        app_id = app["id"]
        path = app.get("web_ui_path") or "/"
        expected = f"http://192.168.1.29:{port}{path}"
        if app.get("web_ui_url") != expected:
            problems.append(f"{app_id}: web_ui_url {app.get('web_ui_url')!r}, expected {expected!r}")
        if app.get("open_url") != f"/api/apps/{app_id}/open":
            problems.append(f"{app_id}: missing/wrong open_url {app.get('open_url')!r}")

        redirect = run(
            [
                "curl",
                "-sS",
                "-D",
                "-",
                "-o",
                "/dev/null",
                "--max-time",
                "10",
                "-H",
                f"Host: {LAN_HOST}",
                f"{DASHBOARD}/api/apps/{app_id}/open",
            ]
        )
        status, location = parse_redirect_response(redirect.stdout)
        if redirect.returncode != 0:
            detail = redirect.stderr.strip() or redirect.stdout.strip() or "request failed"
            problems.append(f"{app_id}: open redirect request failed: {detail}")
        elif status != "302" or location != expected:
            problems.append(
                f"{app_id}: open redirect expected HTTP 302 Location {expected!r}; "
                f"observed status {status!r}, Location {location!r}"
            )

    if problems:
        print("Dashboard watchdog found problems:\n" + "\n".join(f"- {p}" for p in problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
