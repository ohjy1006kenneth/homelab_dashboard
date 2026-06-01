from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.database import DATA_DIR, PROJECT_DIR, get_connection

router = APIRouter(prefix="/api/agents", tags=["agents"])
CONFIG_PATH = PROJECT_DIR / "dashboard.config.json"
LOG_DIR = DATA_DIR / "agent_logs"
RUNNING: dict[str, subprocess.Popen[str]] = {}
RUN_LOGS: dict[str, Path] = {}


def _ensure_table() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_run (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL,
                log_path TEXT,
                summary_line TEXT
            )
            """
        )
        conn.commit()


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {"agents": []}


def _agents() -> list[dict]:
    return _config().get("agents", [])


def _agent(agent_id: str) -> dict:
    for agent in _agents():
        if agent.get("id") == agent_id:
            return agent
    raise HTTPException(status_code=404, detail="Agent not found")


def _reap() -> None:
    _ensure_table()
    for run_id, proc in list(RUNNING.items()):
        code = proc.poll()
        if code is None:
            continue
        status = "success" if code == 0 else "error"
        log_path = RUN_LOGS.get(run_id)
        summary = ""
        if log_path and log_path.exists():
            lines = [line.strip() for line in log_path.read_text(errors="replace").splitlines() if line.strip()]
            summary = lines[-1][:300] if lines else ""
        with get_connection() as conn:
            conn.execute(
                "UPDATE agent_run SET ended_at = ?, status = ?, summary_line = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), status, summary, run_id),
            )
            conn.commit()
        RUNNING.pop(run_id, None)


@router.get("")
def list_agents() -> list[dict]:
    _reap()
    _ensure_table()
    rows = []
    with get_connection() as conn:
        latest = {row["agent_id"]: dict(row) for row in conn.execute("SELECT * FROM agent_run ORDER BY started_at ASC")}
    for agent in _agents():
        current = next((run_id for run_id, proc in RUNNING.items() if proc.poll() is None and latest.get(agent["id"], {}).get("id") == run_id), None)
        item = dict(agent)
        item["status"] = "running" if current else latest.get(agent["id"], {}).get("status", "idle")
        item["last_run"] = latest.get(agent["id"])
        item["current_run_id"] = current
        rows.append(item)
    return rows


@router.post("/{agent_id}/run")
def run_agent(agent_id: str) -> dict:
    _reap()
    _ensure_table()
    agent = _agent(agent_id)
    script = Path(agent.get("script", ""))
    if not script.exists():
        raise HTTPException(status_code=404, detail="Agent script not found")
    if any(proc.poll() is None for run_id, proc in RUNNING.items() if run_id.startswith(f"{agent_id}:")):
        raise HTTPException(status_code=409, detail="Agent already running")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{agent_id}:{uuid.uuid4().hex[:10]}"
    log_path = LOG_DIR / f"{run_id.replace(':', '_')}.log"
    log_file = log_path.open("w", encoding="utf-8")
    cmd = ["bash", str(script)] if script.suffix in {".sh", ".bash"} else ["python3", str(script)]
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True, cwd=PROJECT_DIR, preexec_fn=os.setsid)
    RUNNING[run_id] = proc
    RUN_LOGS[run_id] = log_path
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO agent_run (id, agent_id, started_at, status, log_path) VALUES (?, ?, ?, 'running', ?)",
            (run_id, agent_id, datetime.now(timezone.utc).isoformat(), str(log_path)),
        )
        conn.commit()
    return {"ok": True, "run_id": run_id}


@router.post("/{agent_id}/stop")
def stop_agent(agent_id: str) -> dict:
    stopped = []
    for run_id, proc in list(RUNNING.items()):
        if run_id.startswith(f"{agent_id}:") and proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            stopped.append(run_id)
    time.sleep(0.2)
    _reap()
    return {"ok": True, "stopped": stopped}


@router.get("/{agent_id}/history")
def history(agent_id: str) -> dict:
    _agent(agent_id)
    _reap()
    _ensure_table()
    with get_connection() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM agent_run WHERE agent_id = ? ORDER BY started_at DESC LIMIT 25", (agent_id,))]
    for row in rows:
        path = Path(row.get("log_path") or "")
        row["log_tail"] = path.read_text(errors="replace")[-4000:] if path.exists() else ""
    return {"agent_id": agent_id, "runs": rows}


@router.get("/{agent_id}/logs/stream")
def logs_stream(agent_id: str) -> StreamingResponse:
    _agent(agent_id)

    def events():
        last = 0
        for _ in range(240):
            _reap()
            run_id = next((rid for rid, proc in RUNNING.items() if rid.startswith(f"{agent_id}:") and proc.poll() is None), None)
            if not run_id:
                yield "event: done\ndata: no running agent\n\n"
                return
            path = RUN_LOGS.get(run_id)
            if path and path.exists():
                text = path.read_text(errors="replace")
                if len(text) > last:
                    yield f"data: {json.dumps(text[last:])}\n\n"
                    last = len(text)
            time.sleep(1)
        yield "event: done\ndata: stream timeout\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
