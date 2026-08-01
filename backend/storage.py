from __future__ import annotations

import os
import subprocess
from pathlib import Path


class StorageSafetyError(RuntimeError):
    """Raised when storage-dependent operations are unsafe to run."""


def storage_mount_is_safe(path: Path = Path("/srv/storage"), expected_uuid: str | None = None) -> bool:
    """Fail closed unless path is a mountpoint with the configured UUID."""
    expected = expected_uuid or os.environ.get("DASHBOARD_STORAGE_UUID")
    if not expected:
        return False
    try:
        if subprocess.run(["mountpoint", "-q", str(path)], check=False).returncode != 0:
            return False
        result = subprocess.run(
            ["findmnt", "-n", "-o", "UUID", "--target", str(path)],
            check=False, capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == expected
    except (OSError, subprocess.TimeoutExpired):
        return False


def compose_requires_storage(compose: dict) -> bool:
    services = compose.get("services") or {}
    if not isinstance(services, dict):
        return False
    for service in services.values():
        if not isinstance(service, dict):
            continue
        volumes = service.get("volumes") or []
        for volume in volumes:
            source = volume.get("source") if isinstance(volume, dict) else str(volume).split(":", 1)[0]
            if isinstance(source, str) and (source == "/srv/storage" or source.startswith("/srv/storage/")):
                return True
    return False


def require_safe_storage(compose: dict) -> None:
    if compose_requires_storage(compose) and not storage_mount_is_safe():
        raise StorageSafetyError(
            "storage-dependent app refused: /srv/storage is not mounted with the configured DASHBOARD_STORAGE_UUID"
        )
