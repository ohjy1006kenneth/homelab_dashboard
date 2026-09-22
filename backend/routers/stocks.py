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
PILOT_TICKERS = ["AAPL", "AMD", "NVDA", "MSFT"]


class PacketNotFoundError(Exception):
    """Requested run_id does not exist or was not provided."""


class PacketIntegrityError(Exception):
    """Packet metadata cannot be discovered or is corrupt."""


class PacketContractError(Exception):
    """Packet contents violate the expected schema."""


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


def _extract_packet_dates(run_id: str) -> tuple[str, str]:
    """Extract from_date/to_date from feature filenames in the run directory.

    Filenames like ``layer1-daily-2026-09-10-2026-09-10`` encode
    ``prefix-YYYY-MM-DD-YYYY-MM-DD``; we use the earliest as from_date
    and the latest as to_date.  Falls back to the run_id itself.
    """
    import re
    features_dir = LOCAL_R2_ROOT / "features" / run_id
    if not features_dir.is_dir():
        return run_id, run_id

    pattern = re.compile(r"(\d{4}-\d{2}-\d{2})-(\d{4}-\d{2}-\d{2})$")
    dates: list[str] = []
    for feat_sub in features_dir.iterdir():
        if not feat_sub.is_dir():
            continue
        for f in feat_sub.iterdir():
            m = pattern.search(f.name)
            if m:
                dates.append(m.group(1))
                dates.append(m.group(2))

    if not dates:
        return run_id, run_id

    return min(dates), max(dates)


def _build_review_options() -> dict[str, Any]:
    """Build review options (candidates + default) from available packet metadata."""
    run_ids = _find_run_ids()
    if not run_ids:
        return {"candidates": [], "default_selection": {}}

    candidates = []
    for run_id in run_ids:
        from_date, to_date = _extract_packet_dates(run_id)
        label_dates = from_date if from_date == to_date else f"{from_date} to {to_date}"
        candidates.append({
            "id": run_id,
            "label": f"{', '.join(PILOT_TICKERS)} · {label_dates}",
            "from_date": from_date,
            "to_date": to_date,
            "tickers": list(PILOT_TICKERS),
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
    run_id: str | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    tickers: str = Query("AAPL"),
):
    """Return the full semantic review audit payload for the specified packet."""
    tickers_list = list(dict.fromkeys(
        t.strip().upper() for t in tickers.split(",") if t.strip()
    )) or PILOT_TICKERS[:1]

    if not run_id:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketNotFoundError",
            "reason": "run_id is required; select a packet from /api/stocks/review-options",
            "payload": {},
            "query": {"from_date": from_date, "to_date": to_date, "tickers": tickers_list},
        }

    try:
        available = _find_run_ids()
    except Exception as exc:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketIntegrityError",
            "reason": f"Packet discovery failed: {exc}",
            "payload": {},
            "query": {"from_date": from_date, "to_date": to_date, "tickers": tickers_list},
        }

    if run_id not in available:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketNotFoundError",
            "reason": f"Packet '{run_id}' not found; available: {available}",
            "payload": {},
            "query": {"from_date": from_date, "to_date": to_date, "tickers": tickers_list},
        }

    # Default from_date/to_date from the packet's actual dates
    pkt_from, pkt_to = _extract_packet_dates(run_id)
    if from_date is None:
        from_date = pkt_from
    if to_date is None:
        to_date = pkt_to

    try:
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for ticker in tickers_list:
            try:
                results[ticker] = _build_audit_payload(run_id, from_date, to_date, ticker)
            except RuntimeError as exc:
                errors[ticker] = str(exc)

        if not results:
            # All tickers failed — re-raise so outer handler returns PacketContractError
            raise RuntimeError("; ".join(f"{t}: {e}" for t, e in errors.items()))

        # Aggregate counts across all successful tickers
        aggregated_counts: dict[str, Any] = {}
        for tkr, tkr_payload in results.items():
            for topic, count in tkr_payload.get("counts", {}).items():
                aggregated_counts[topic] = aggregated_counts.get(topic, 0) + count

        return {
            "ok": True,
            "status": "fail" if errors else "pass",
            "run_id": run_id,
            "query": {
                "from_date": from_date,
                "to_date": to_date,
                "tickers": tickers_list,
            },
            "payload": {
                "tickers": results,
                **({"errors": errors} if errors else {}),
            },
            "review_options": _build_review_options(),
            "review_counts": aggregated_counts,
        }
    except RuntimeError as exc:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketContractError",
            "reason": str(exc),
            "run_id": run_id,
            "payload": {},
            "query": {
                "from_date": from_date,
                "to_date": to_date,
                "tickers": tickers_list,
            },
        }
