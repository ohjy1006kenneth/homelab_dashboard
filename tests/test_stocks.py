"""Endpoint tests for /api/stocks/review-options and /api/stocks/audit."""
from __future__ import annotations

import json
import subprocess as sp_mod
from unittest import mock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def mock_run_ids():
    """Return a stable list of run IDs for tests."""
    return ["2026-09-11", "2026-09-10"]


def mock_audit_payload():
    """Return a minimal but valid _build_dashboard_payload result."""
    return {
        "status": "pass",
        "counts": {"total": 50, "pass": 45, "fail": 5},
        "ticker": "AAPL",
        "data": [{"date": "2026-09-10", "signal": "buy"}],
    }


# ── /api/stocks/review-options ────────────────────────────────────────────────

class TestReviewOptions:
    """review-options route tests."""

    def test_returns_candidates_and_default(self):
        """Happy path: features dirs exist, returns candidates + default."""
        with mock.patch.object(sp_mod, "run"), \
             mock.patch("backend.routers.stocks._find_run_ids", return_value=mock_run_ids()):
            resp = client.get("/api/stocks/review-options")
        data = resp.json()
        assert resp.status_code == 200
        assert "candidates" in data
        assert "default_selection" in data
        assert len(data["candidates"]) == 2
        assert data["default_selection"]["id"] == "2026-09-11"
        assert data["candidates"][0]["id"] == "2026-09-11"
        assert data["candidates"][0]["from_date"] == "2025-01-01"
        assert data["candidates"][0]["tickers"] == ["AAPL"]

    def test_empty_run_root_returns_empty(self):
        """When no features dirs exist, returns empty candidates."""
        with mock.patch.object(sp_mod, "run"), \
             mock.patch("backend.routers.stocks._find_run_ids", return_value=[]):
            resp = client.get("/api/stocks/review-options")
        data = resp.json()
        assert resp.status_code == 200
        assert data["candidates"] == []
        assert data["default_selection"] == {}

    def test_error_includes_error_field(self):
        """When _find_run_ids raises, response contains error info."""
        with mock.patch("backend.routers.stocks._find_run_ids", side_effect=OSError("no access")):
            resp = client.get("/api/stocks/review-options")
        data = resp.json()
        assert resp.status_code == 200
        assert data["candidates"] == []
        assert data["default_selection"] == {}
        assert "error" in data
        assert "no access" in data["error"]


# ── /api/stocks/audit ─────────────────────────────────────────────────────────

class TestAudit:
    """audit route tests."""

    def _mock_audit(self, payload=None):
        """Context manager that mocks _find_run_ids + _run_trader_subprocess."""
        if payload is None:
            payload = mock_audit_payload()
        stdout = json.dumps(payload, default=str)
        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = stdout
        fake_result.stderr = ""
        return mock.patch.object(
            sp_mod, "run", return_value=fake_result
        ), mock.patch("backend.routers.stocks._find_run_ids", return_value=mock_run_ids())

    def test_audit_success(self):
        """Happy path: returns ok, payload, query, counts."""
        pay = mock_audit_payload()
        mock_run, mock_ids = self._mock_audit(pay)
        with mock_run, mock_ids:
            resp = client.get(
                "/api/stocks/audit",
                params={"from_date": "2025-01-01", "to_date": "2026-09-21", "tickers": "AAPL"},
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is True
        assert data["status"] == "pass"
        assert data["query"]["from_date"] == "2025-01-01"
        assert data["query"]["to_date"] == "2026-09-21"
        assert data["query"]["tickers"] == ["AAPL"]
        assert "payload" in data
        assert data["review_counts"]["total"] == 50

    def test_audit_echoes_requested_ticker(self):
        """Requested ticker is isolated in query echo regardless of others."""
        pay = mock_audit_payload()
        mock_run, mock_ids = self._mock_audit(pay)
        with mock_run, mock_ids:
            resp = client.get(
                "/api/stocks/audit",
                params={"from_date": "2025-01-01", "to_date": "2026-09-21", "tickers": "AMD,MSFT"},
            )
        data = resp.json()
        assert data["query"]["tickers"] == ["AMD"]

    def test_audit_uppercases_ticker(self):
        """Ticker normalization: lower -> upper."""
        pay = mock_audit_payload()
        mock_run, mock_ids = self._mock_audit(pay)
        with mock_run, mock_ids:
            resp = client.get(
                "/api/stocks/audit",
                params={"from_date": "2025-01-01", "to_date": "2026-09-21", "tickers": "aapl"},
            )
        data = resp.json()
        assert data["query"]["tickers"] == ["AAPL"]

    def test_audit_subprocess_nonzero_returns_error_schema(self):
        """When trader subprocess returns non-zero, returns graceful error."""
        fake_result = mock.Mock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "Traceback: ModuleNotFoundError"
        with mock.patch.object(sp_mod, "run", return_value=fake_result), \
             mock.patch("backend.routers.stocks._find_run_ids", return_value=mock_run_ids()):
            resp = client.get(
                "/api/stocks/audit",
                params={"from_date": "2025-01-01", "to_date": "2026-09-21", "tickers": "AAPL"},
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is False
        assert data["status"] == "fail"
        assert "reason" in data
        assert "payload" in data
        assert "query" in data

    def test_audit_subprocess_invalid_json_returns_error_schema(self):
        """When subprocess stdout is not valid JSON, returns graceful error."""
        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = "not-json-at-all"
        fake_result.stderr = ""
        with mock.patch.object(sp_mod, "run", return_value=fake_result), \
             mock.patch("backend.routers.stocks._find_run_ids", return_value=mock_run_ids()):
            resp = client.get(
                "/api/stocks/audit",
                params={"from_date": "2025-01-01", "to_date": "2026-09-21", "tickers": "NVDA"},
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is False
        assert data["status"] == "fail"
        assert "reason" in data
        assert data["query"]["tickers"] == ["NVDA"]

    def test_audit_timeout_returns_error_schema(self):
        """When subprocess times out, returns graceful error."""
        with mock.patch.object(sp_mod, "run", side_effect=sp_mod.TimeoutExpired("cmd", 120)), \
             mock.patch("backend.routers.stocks._find_run_ids", return_value=mock_run_ids()):
            resp = client.get(
                "/api/stocks/audit",
                params={"from_date": "2025-01-01", "to_date": "2026-09-21", "tickers": "AAPL"},
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is False
        assert data["status"] == "fail"
        assert "reason" in data
        assert "payload" in data
        assert "query" in data

    def test_audit_no_run_ids_falls_back_to_default(self):
        """When no run dirs found, still attempts audit with fallback run_id."""
        pay = mock_audit_payload()
        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = json.dumps(pay, default=str)
        fake_result.stderr = ""
        with mock.patch.object(sp_mod, "run", return_value=fake_result), \
             mock.patch("backend.routers.stocks._find_run_ids", return_value=[]):
            resp = client.get(
                "/api/stocks/audit",
                params={"from_date": "2025-01-01", "to_date": "2026-09-21", "tickers": "AAPL"},
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is True

    def test_audit_default_params(self):
        """Default query params produce valid response."""
        pay = mock_audit_payload()
        mock_run, mock_ids = self._mock_audit(pay)
        with mock_run, mock_ids:
            resp = client.get("/api/stocks/audit")
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is True
        assert data["query"]["from_date"] == "2025-01-01"
        assert data["query"]["tickers"] == ["AAPL"]

    def test_audit_review_options_included_in_response(self):
        """Response includes review_options field."""
        pay = mock_audit_payload()
        mock_run, mock_ids = self._mock_audit(pay)
        with mock_run, mock_ids:
            resp = client.get(
                "/api/stocks/audit",
                params={"from_date": "2025-01-01", "to_date": "2026-09-21", "tickers": "AAPL"},
            )
        data = resp.json()
        assert "review_options" in data
        assert "candidates" in data["review_options"]
        assert "default_selection" in data["review_options"]


# ── sys.path ordering ─────────────────────────────────────────────────────────

class TestSubprocessPathOrder:
    """Verify sys.path.insert runs before app.lab import."""

    def test_syspath_before_import(self):
        """The generated subprocess code must insert TRADER_ROOT before importing app.lab."""
        from backend.routers.stocks import _build_audit_payload
        # Trigger code generation (don't actually run subprocess)
        original_run = sp_mod.run
        gen_code = None

        def capture_run(cmd, **kwargs):
            nonlocal gen_code
            # cmd = [python, "-c", code_string]
            gen_code = cmd[2] if len(cmd) > 2 else ""
            fake = mock.Mock()
            fake.returncode = 0
            fake.stdout = json.dumps({"status": "pass", "counts": {}, "data": []})
            fake.stderr = ""
            return fake

        with mock.patch("backend.routers.stocks.subprocess.run", side_effect=capture_run), \
             mock.patch("backend.routers.stocks._find_run_ids", return_value=["2026-09-11"]):
            client.get("/api/stocks/audit", params={"tickers": "AAPL"})

        assert gen_code is not None
        # sys.path.insert must appear BEFORE the app.lab import
        syspath_pos = gen_code.index("sys.path.insert")
        import_pos = gen_code.index("from app.lab")
        assert syspath_pos < import_pos, \
            f"sys.path.insert at pos {syspath_pos} must come before import at pos {import_pos}"
