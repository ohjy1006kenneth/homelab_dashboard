from __future__ import annotations

import shutil
import time
from pathlib import Path

import psutil
from fastapi import APIRouter

router = APIRouter(prefix="/api/metrics", tags=["metrics"])
BOOT_TIME = psutil.boot_time()


def _cpu_temp_c() -> float | None:
    thermal = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(thermal.read_text().strip()) / 1000, 1)
    except (FileNotFoundError, PermissionError, ValueError):
        return None


@router.get("")
def get_metrics() -> dict[str, float | None]:
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    return {
        "cpu_pct": psutil.cpu_percent(interval=0.1),
        "ram_used_gb": round(vm.used / 1024**3, 2),
        "ram_total_gb": round(vm.total / 1024**3, 2),
        "disk_used_gb": round(disk.used / 1024**3, 2),
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "cpu_temp_c": _cpu_temp_c(),
        "uptime_hours": round((time.time() - BOOT_TIME) / 3600, 1),
    }
