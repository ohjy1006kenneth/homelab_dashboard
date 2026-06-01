from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterator

import psutil
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/metrics", tags=["metrics"])
BOOT_TIME = psutil.boot_time()
NET_BASELINE = psutil.net_io_counters()
NET_BASELINE_AT = time.time()
PROJECT_DIR = Path(__file__).resolve().parents[2]
WATCHDOG = Path("/home/juyoungoh/.hermes/profiles/dashcraft/scripts/dashboard_watchdog.py")


def _cpu_temp_c() -> float | None:
    thermal = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(thermal.read_text().strip()) / 1000, 1)
    except (FileNotFoundError, PermissionError, ValueError):
        return None


def _docker_counts() -> dict[str, int | None]:
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.State}}"],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        return {"total": None, "running": None, "stopped": None}
    states = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {
        "total": len(states),
        "running": sum(1 for state in states if state == "running"),
        "stopped": sum(1 for state in states if state != "running"),
    }


def _disk_usage(path: str) -> dict[str, str | float | bool]:
    mount = Path(path)
    exists = mount.exists()
    usage = shutil.disk_usage(path if exists else "/")
    return {
        "path": path,
        "exists": exists,
        "used_gb": round(usage.used / 1024**3, 2),
        "total_gb": round(usage.total / 1024**3, 2),
        "pct": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
    }


def current_metrics() -> dict:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    root_disk = shutil.disk_usage("/")
    net = psutil.net_io_counters()
    elapsed = max(time.time() - NET_BASELINE_AT, 1)
    load1, load5, load15 = psutil.getloadavg()
    docker = _docker_counts()
    return {
        "cpu_pct": psutil.cpu_percent(interval=0.1),
        "cpu_count": psutil.cpu_count(),
        "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "ram_used_gb": round(vm.used / 1024**3, 2),
        "ram_total_gb": round(vm.total / 1024**3, 2),
        "ram_pct": round(vm.percent, 1),
        "swap_used_gb": round(swap.used / 1024**3, 2),
        "swap_total_gb": round(swap.total / 1024**3, 2),
        "swap_pct": round(swap.percent, 1),
        "disk_used_gb": round(root_disk.used / 1024**3, 2),
        "disk_total_gb": round(root_disk.total / 1024**3, 2),
        "disk_pct": round((root_disk.used / root_disk.total) * 100, 1) if root_disk.total else 0,
        "mounts": [_disk_usage("/"), _disk_usage("/home/juyoungoh/nas")],
        "cpu_temp_c": _cpu_temp_c(),
        "uptime_hours": round((time.time() - BOOT_TIME) / 3600, 1),
        "network": {
            "sent_gb": round(net.bytes_sent / 1024**3, 2),
            "recv_gb": round(net.bytes_recv / 1024**3, 2),
            "avg_sent_kbps": round(((net.bytes_sent - NET_BASELINE.bytes_sent) / elapsed) / 1024, 1),
            "avg_recv_kbps": round(((net.bytes_recv - NET_BASELINE.bytes_recv) / elapsed) / 1024, 1),
        },
        "docker": docker,
    }


@router.get("")
def get_metrics() -> dict:
    return current_metrics()


def _metric_events() -> Iterator[str]:
    while True:
        yield f"data: {json.dumps(current_metrics())}\n\n"
        time.sleep(3)


@router.get("/stream")
def stream_metrics() -> StreamingResponse:
    return StreamingResponse(_metric_events(), media_type="text/event-stream")


@router.get("/ops")
def ops_status() -> dict:
    service = subprocess.run(["systemctl", "is-active", "lab-dashboard.service"], text=True, capture_output=True, check=False, timeout=5)
    watchdog = {"available": WATCHDOG.exists(), "ok": None, "output": ""}
    if WATCHDOG.exists():
        result = subprocess.run(["python3", str(WATCHDOG)], text=True, capture_output=True, check=False, timeout=60, cwd=PROJECT_DIR)
        watchdog = {"available": True, "ok": result.returncode == 0 and not result.stdout.strip(), "output": (result.stdout + result.stderr).strip()[:4000]}
    service_state = service.stdout.strip() or service.stderr.strip() or "unknown"
    if service_state != "active":
        live = subprocess.run(["pgrep", "-f", "uvicorn backend.main:app.*--port 8765"], text=True, capture_output=True, check=False, timeout=5)
        if live.stdout.strip():
            service_state = "manual"
    return {
        "dashboard_service": service_state,
        "watchdog": watchdog,
        "checked_at": time.time(),
    }
