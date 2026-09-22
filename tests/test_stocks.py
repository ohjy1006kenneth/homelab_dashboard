"""Regression tests for packet-backed Stock audit and review-options endpoints."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

TRADER_ROOT = Path("/home/juyoungoh/AI-Stock-Trader")
LOCAL_R2_ROOT = TRADER_ROOT / "data" / "runtime" / "local_r2"


class TestReviewOptions:
    """Test /api/stocks/review-options derives choices from packet metadata."""

    def test_review_options_returns_ok(self):
        """review-options returns ok: True with candidates and default_selection."""
        resp = client.get("/api/stocks/review-options")
        data = resp.json()
        assert data["ok"] is True

    def test_review_options_has_candidates(self):
        """review-options returns a non-empty candidates list."""
        resp = client.get("/api/stocks/review-options")
        data = resp.json()
        assert isinstance(data["candidates"], list)
        assert len(data["candidates"]) > 0

    def test_review_options_exposes_all_pilot_tickers(self):
        """Each candidate in review-options includes all 4 pilot tickers."""
        resp = client.get("/api/stocks/review-options")
        data = resp.json()
        pilot = ["AAPL", "AMD", "NVDA", "MSFT"]
        for candidate in data["candidates"]:
            assert candidate["tickers"] == pilot

    def test_review_options_default_selection(self):
        """review-options returns a default_selection with valid packet fields."""
        resp = client.get("/api/stocks/review-options")
        data = resp.json()
        assert "default_selection" in data
        default = data["default_selection"]
        assert "run_id" in default
        assert "from_date" in default
        assert "to_date" in default
        assert "tickers" in default

    def test_review_options_candidate_has_run_id(self):
        """Each candidate carries a run_id for downstream audit selection."""
        resp = client.get("/api/stocks/review-options")
        data = resp.json()
        for candidate in data["candidates"]:
            assert "run_id" in candidate
            assert isinstance(candidate["run_id"], str)
            assert len(candidate["run_id"]) > 0


class TestAuditWithRunId:
    """Test /api/stocks/audit accepts and obeys explicit run_id."""

    def test_audit_without_run_id_returns_packet_not_found(self):
        """Missing run_id returns PacketNotFoundError, not silent fallback."""
        resp = client.get("/api/stocks/audit")
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "PacketNotFoundError"
        assert "run_id is required" in data["reason"]

    def test_audit_with_invalid_run_id_returns_packet_not_found(self):
        """Non-existent run_id returns PacketNotFoundError."""
        resp = client.get("/api/stocks/audit?run_id=nonexistent-packet")
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "PacketNotFoundError"
        assert "nonexistent-packet" in data["reason"]

    def test_audit_with_valid_run_id_includes_correlation(self):
        """Valid run_id response includes run_id and query correlation metadata."""
        # Get actual run_ids first
        opts_resp = client.get("/api/stocks/review-options")
        opts_data = opts_resp.json()
        if not opts_data.get("candidates"):
            pytest.skip("No available packets to test with")
        run_id = opts_data["candidates"][0]["run_id"]

        resp = client.get(f"/api/stocks/audit?run_id={run_id}")
        data = resp.json()
        assert data.get("run_id") == run_id
        assert "query" in data
        assert data["query"]["from_date"] == "2025-01-01"
        assert "tickers" in data["query"]

    def test_audit_preserves_exact_query_params(self):
        """Audit response echoes back the exact from_date, to_date, tickers requested."""
        opts_resp = client.get("/api/stocks/review-options")
        opts_data = opts_resp.json()
        if not opts_data.get("candidates"):
            pytest.skip("No available packets to test with")
        run_id = opts_data["candidates"][0]["run_id"]

        resp = client.get(
            "/api/stocks/audit",
            params={
                "run_id": run_id,
                "from_date": "2026-01-01",
                "to_date": "2026-06-01",
                "tickers": "NVDA,MSFT",
            },
        )
        data = resp.json()
        assert data["query"]["from_date"] == "2026-01-01"
        assert data["query"]["to_date"] == "2026-06-01"
        assert data["query"]["tickers"] == ["NVDA", "MSFT"]

    def test_audit_success_includes_review_options(self):
        """Successful audit response carries review_options for UI integration."""
        opts_resp = client.get("/api/stocks/review-options")
        opts_data = opts_resp.json()
        if not opts_data.get("candidates"):
            pytest.skip("No available packets to test with")
        run_id = opts_data["candidates"][0]["run_id"]

        resp = client.get(f"/api/stocks/audit?run_id={run_id}")
        data = resp.json()
        if data.get("ok"):
            assert "review_options" in data


class TestPacketErrorClasses:
    """Test that the three packet error classes exist and are used correctly."""

    def test_packet_not_found_error_exists(self):
        """PacketNotFoundError is defined in stocks router."""
        from backend.routers.stocks import PacketNotFoundError
        assert issubclass(PacketNotFoundError, Exception)

    def test_packet_integrity_error_exists(self):
        """PacketIntegrityError is defined in stocks router."""
        from backend.routers.stocks import PacketIntegrityError
        assert issubclass(PacketIntegrityError, Exception)

    def test_packet_contract_error_exists(self):
        """PacketContractError is defined in stocks router."""
        from backend.routers.stocks import PacketContractError
        assert issubclass(PacketContractError, Exception)

    def test_audit_contract_error_on_subprocess_failure(self):
        """When subprocess fails, audit returns PacketContractError."""
        opts_resp = client.get("/api/stocks/review-options")
        opts_data = opts_resp.json()
        if not opts_data.get("candidates"):
            pytest.skip("No available packets to test with")
        run_id = opts_data["candidates"][0]["run_id"]

        with patch(
            "backend.routers.stocks._run_trader_subprocess",
            side_effect=RuntimeError("forced contract error"),
        ):
            resp = client.get(f"/api/stocks/audit?run_id={run_id}")
            data = resp.json()
            assert data["ok"] is False
            assert data["error"] == "PacketContractError"

    def test_audit_integrity_error_on_discovery_failure(self):
        """When packet discovery fails, audit returns PacketIntegrityError."""
        with patch(
            "backend.routers.stocks._find_run_ids",
            side_effect=OSError("disk read failed"),
        ):
            resp = client.get("/api/stocks/audit?run_id=any-packet")
            data = resp.json()
            assert data["ok"] is False
            assert data["error"] == "PacketIntegrityError"


class TestNoSilentSubstitution:
    """Verify the backend never silently substitutes another packet."""

    def test_audit_uses_exact_run_id_not_latest(self):
        """Audit with a specific run_id returns that same run_id, not another."""
        opts_resp = client.get("/api/stocks/review-options")
        opts_data = opts_resp.json()
        candidates = opts_data.get("candidates", [])
        if len(candidates) < 2:
            pytest.skip("Need at least 2 packets to verify no substitution")
        # Use the second (non-default) run_id
        run_id = candidates[1]["run_id"]
        resp = client.get(f"/api/stocks/audit?run_id={run_id}")
        data = resp.json()
        if data.get("ok"):
            assert data["run_id"] == run_id

    def test_audit_never_returns_ok_with_missing_run_id(self):
        """Audit without run_id never returns ok=True (no silent fallback)."""
        resp = client.get("/api/stocks/audit")
        data = resp.json()
        assert data["ok"] is False


class TestFourTickerDisplayedEvidence:
    """Ensure all four pilot tickers are available for display."""

    def test_review_options_has_four_ticker_candidates(self):
        """Candidates expose evidence for AAPL, AMD, NVDA, and MSFT."""
        resp = client.get("/api/stocks/review-options")
        data = resp.json()
        for candidate in data["candidates"]:
            assert "AAPL" in candidate["tickers"]
            assert "AMD" in candidate["tickers"]
            assert "NVDA" in candidate["tickers"]
            assert "MSFT" in candidate["tickers"]

    def test_audit_accepts_multiticker_query(self):
        """Audit endpoint accepts all 4 tickers in query parameter."""
        opts_resp = client.get("/api/stocks/review-options")
        opts_data = opts_resp.json()
        if not opts_data.get("candidates"):
            pytest.skip("No available packets")
        run_id = opts_data["candidates"][0]["run_id"]

        resp = client.get(
            "/api/stocks/audit",
            params={
                "run_id": run_id,
                "tickers": "AAPL,AMD,NVDA,MSFT",
            },
        )
        data = resp.json()
        tickers = data.get("query", {}).get("tickers", [])
        assert len(tickers) == 4
