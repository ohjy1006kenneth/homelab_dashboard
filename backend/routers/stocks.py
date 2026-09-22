"""Proxy routes for AI-Stock-Trader semantic review dashboard."""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter()

TRADER_VENV_PYTHON = Path("/home/juyoungoh/AI-Stock-Trader/.venv/bin/python")
TRADER_ROOT = Path("/home/juyoungoh/AI-Stock-Trader")
LOCAL_R2_ROOT = TRADER_ROOT / "data" / "runtime" / "local_r2"
SUBPROCESS_TIMEOUT = 120  # seconds


def _find_run_ids() -> list[str]:
    """Find available Layer 1 run IDs by scanning local_r2/features/."""
    features_dir = LOCAL_R2_ROOT / "features"
    if not features_dir.exists():
        return []
    run_dirs = sorted(features_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return [d.name for d in run_dirs if d.is_dir()]


def _run_trader_subprocess(code: str, args: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    """Run Python code in the AI-Stock-Trader venv."""
    return subprocess.run(
        [str(TRADER_VENV_PYTHON), "-c", code] + list(args),
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
        cwd=str(TRADER_ROOT),
    )


def _build_audit_payload(
    run_id: str,
    from_date: str,
    to_date: str,
    ticker: str,
) -> dict[str, Any]:
    """Build the full audit payload via subprocess."""
    code = f"""
import sys
sys.path.insert(0, '{TRADER_ROOT}')
import json
from pathlib import Path
from app.lab.semantic_review_dashboard import _build_dashboard_payload

payload = _build_dashboard_payload(
    run_id={json.dumps(run_id)},
    from_date={json.dumps(from_date)},
    to_date={json.dumps(to_date)},
    ticker={json.dumps(ticker)},
    local_root=Path('{LOCAL_R2_ROOT}'),
)
print(json.dumps(payload, default=str))
"""
    result = _run_trader_subprocess(code)
    if result.returncode != 0:
        raise RuntimeError(f"Trader subprocess failed: {result.stderr[-500:]}")
    return json.loads(result.stdout)


def _build_review_options() -> dict[str, Any]:
    """Build review options (candidates + default) via subprocess."""
    run_ids = _find_run_ids()
    if not run_ids:
        return {"candidates": [], "default_selection": {}}

    candidates = []
    for run_id in run_ids:
        candidates.append({
            "id": run_id,
            "label": f"AAPL \u00b7 2025-01-01 to {run_id}",
            "from_date": "2025-01-01",
            "to_date": run_id,
            "tickers": ["AAPL"],
            "run_id": run_id,
        })

    return {
        "candidates": candidates,
        "default_selection": candidates[0] if candidates else {},
    }


@router.get("/api/stocks/review-options")
def review_options():
    """Return available review packets and default selection."""
    try:
        result = _build_review_options()
        return {**result, "ok": True}
    except Exception as exc:
        return {
            "ok": False,
            "status": "fail",
            "reason": f"{type(exc).__name__}: {exc}",
            "candidates": [],
            "default_selection": {},
        }


@router.get("/api/stocks/audit")
def audit(
    from_date: str = Query("2025-01-01"),
    to_date: str = Query(default_factory=lambda: date.today().isoformat()),
    tickers: str = Query("AAPL"),
):
    """Return the full semantic review audit payload."""
    ticker = tickers.split(",")[0].strip().upper()
    try:
        run_ids = _find_run_ids()
        run_id = run_ids[0] if run_ids else "2026-09-11"
        payload = _build_audit_payload(run_id, from_date, to_date, ticker)
        review_opts = _build_review_options()
        return {
            "ok": True,
            "status": payload.get("status", "pass"),
            "query": {"from_date": from_date, "to_date": to_date, "tickers": [ticker]},
            "payload": payload,
            "review_options": review_opts,
            "review_counts": payload.get("counts", {}),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "fail",
            "reason": f"{type(exc).__name__}: {exc}",
            "payload": {},
            "query": {"from_date": from_date, "to_date": to_date, "tickers": [ticker]},
        }
