from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DashboardPaths:
    """Filesystem contracts for source, mutable state, stacks, and storage."""

    root: Path
    state: Path
    stacks: Path
    appdata: Path
    nas: Path

    @property
    def db(self) -> Path:
        return self.state / "dashboard.db"

    @property
    def config(self) -> Path:
        return self.root / "dashboard.config.json"

    @property
    def apps(self) -> Path:
        return self.root / "apps"


def from_environment() -> DashboardPaths:
    root = Path(os.environ.get("DASHBOARD_ROOT", Path(__file__).resolve().parents[1]))
    state = Path(os.environ.get("DASHBOARD_STATE_DIR", root / "data"))
    return DashboardPaths(
        root=root,
        state=state,
        stacks=Path(os.environ.get("DASHBOARD_STACKS_DIR", root / "apps")),
        appdata=Path(os.environ.get("DASHBOARD_APPDATA_DIR", root / "appdata")),
        nas=Path(os.environ.get("DASHBOARD_NAS_DIR", "/srv/storage/nas")),
    )


PATHS = from_environment()
PROJECT_DIR = PATHS.root
DATA_DIR = PATHS.state
DB_PATH = PATHS.db
APPS_DIR = PATHS.apps
STACKS_DIR = PATHS.stacks
APPDATA_DIR = PATHS.appdata
NAS_DIR = PATHS.nas
