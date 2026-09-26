"""Regression tests for packet-backed Stock audit and review-options endpoints."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import stocks

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
        assert data["reason"] == "Selected packet was not found."

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
        # from_date defaults to the packet's actual dates, not a hard-coded value
        assert "from_date" in data["query"]
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

    def test_audit_response_includes_review_options(self):
        """The exact packet response carries options even when evidence fails closed."""
        opts_resp = client.get("/api/stocks/review-options")
        opts_data = opts_resp.json()
        if not opts_data.get("candidates"):
            pytest.skip("No available packets")
        run_id = opts_data["candidates"][0]["run_id"]

        resp = client.get(f"/api/stocks/audit?run_id={run_id}")
        data = resp.json()
        assert "review_options" in data
        assert data["reviews"][0]["producer_correlation"]["run_id"] == run_id


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

    def test_audit_contract_error_on_adapter_failure(self):
        """Unexpected adapter failures are controlled and do not leak details."""
        opts_resp = client.get("/api/stocks/review-options")
        opts_data = opts_resp.json()
        if not opts_data.get("candidates"):
            pytest.skip("No available packets to test with")
        run_id = opts_data["candidates"][0]["run_id"]

        with patch(
            "backend.routers.stocks._build_audit_payload",
            side_effect=RuntimeError("forced contract error"),
        ):
            resp = client.get(f"/api/stocks/audit?run_id={run_id}")
            data = resp.json()
            assert data["ok"] is False
            assert data["error"] == "SemanticEvidenceUnavailable"
            assert data["ticker_errors"]["AAPL"] == {
                "error": "PacketContractError",
                "reason": "Producer audit payload could not be built.",
            }
            assert "forced contract error" not in json.dumps(data)

    def test_audit_integrity_error_on_discovery_failure(self):
        """When packet discovery fails, audit returns PacketIntegrityError."""
        with patch(
            "backend.routers.stocks._load_packet_catalog",
            side_effect=stocks.PacketIntegrityError("disk read failed"),
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
        assert data.get("ok") is True, f"Expected ok=True but got: {data}"
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


class TestNormalizedMultiTickerReviews:
    """Test the stable per-ticker review adapter returned by the audit route."""

    @staticmethod
    def _catalog() -> dict:
        """Return the minimum packet catalog needed by the route preflight."""
        return {
            "packet-1": {
                "packet": {
                    "run_id": "packet-1",
                    "review_date": "2026-06-18",
                    "tickers": ["AAPL", "AMD", "NVDA", "MSFT"],
                    "ticker_audits": {},
                }
            }
        }

    @staticmethod
    def _producer_payload(ticker: str, index: int) -> dict:
        """Return a representative producer payload with ticker-specific counts."""
        return {
            "controls": {
                "run_id": "packet-1",
                "from_date": "2026-06-18",
                "to_date": "2026-06-18",
                "ticker": ticker,
            },
            "summary": {
                "row_count": index + 10,
                "article_count": index + 4,
                "relevance_gate_row_count": index + 20,
            },
            "run_readiness": {
                "readiness_status": "not_ready_for_final_human_acceptance",
                "smoke_status": "pass",
                "ticker_association_state": {
                    "state": "PASS",
                    "reason": "Producer verified requested-ticker association.",
                },
                "relevance_informativeness_state": {
                    "state": "WARN",
                    "reason": f"{ticker} relevance requires review",
                    "accepted_count": index + 1,
                    "borderline_count": index + 2,
                    "rejected_count": index + 3,
                },
                "topic_review_state": {
                    "state": "NO_DATA",
                    "reason": f"{ticker} has no reviewable topic cluster",
                    "reviewable": False,
                },
            },
            "smoke": {"status": "pass"},
            "gate_cards": [
                {
                    "key": "news_preprocessing",
                    "status": "ready",
                    "message": f"{ticker} ticker association rows loaded",
                    "failure_reasons": [],
                },
                {
                    "key": "news_relevance_gate",
                    "status": "ready",
                    "message": f"{ticker} relevance rows loaded",
                    "failure_reasons": [],
                },
                {
                    "key": "news_sentiment_scored",
                    "status": "ready",
                    "message": f"{ticker} FinBERT rows loaded",
                    "failure_reasons": [],
                },
                {
                    "key": "topic_labels",
                    "status": "blocked",
                    "message": f"{ticker} topic labels are unavailable",
                    "failure_reasons": ["missing_or_incomplete_artifacts"],
                },
            ],
        }

    def test_audit_returns_one_normalized_review_per_successful_ticker(self):
        """Four successful producer payloads become four stable normalized reviews."""
        tickers = ["AAPL", "AMD", "NVDA", "MSFT"]
        payloads = {
            ticker: self._producer_payload(ticker, index)
            for index, ticker in enumerate(tickers)
        }

        with (
            patch(
                "backend.routers.stocks._load_packet_catalog",
                return_value=self._catalog(),
            ),
            patch("backend.routers.stocks._build_review_options", return_value={}),
            patch(
                "backend.routers.stocks._build_audit_payload",
                side_effect=lambda _run_id, _from_date, _to_date, ticker: payloads[ticker],
            ),
        ):
            response = client.get(
                "/api/stocks/audit",
                params={
                    "run_id": "packet-1",
                    "from_date": "2026-06-18",
                    "to_date": "2026-06-18",
                    "tickers": ",".join(tickers),
                },
            )

        data = response.json()
        assert data["ok"] is True
        assert list(data["payload"]["tickers"]) == tickers
        assert [review["ticker"] for review in data["reviews"]] == tickers

        for index, review in enumerate(data["reviews"]):
            ticker = tickers[index]
            assert review["run_id"] == "packet-1"
            assert review["query"] == {
                "from_date": "2026-06-18",
                "to_date": "2026-06-18",
                "ticker": ticker,
            }
            assert review["status"] == "not_ready_for_final_human_acceptance"
            assert review["smoke_status"] == "pass"
            assert review["review_counts"] == {
                # Unknown is not the same as a measured zero.
                "input_article_count": None,
                "sentence_row_count": index + 10,
                "reviewed_article_count": index + 4,
                "relevance_row_count": index + 20,
                "accepted_decision_count": index + 1,
                "borderline_decision_count": index + 2,
                "rejected_decision_count": index + 3,
            }
            assert review["states"]["ticker_association"] == {
                "state": "PASS",
                "reason": "Producer verified requested-ticker association.",
            }
            assert review["states"]["relevance"]["state"] == "WARN"
            assert review["states"]["finbert"]["status"] == "ready"
            assert review["states"]["topic"]["state"] == "NO_DATA"
            assert review["decision_reasons"]["ticker_association"] == [
                "Producer verified requested-ticker association."
            ]
            assert review["decision_reasons"]["relevance"] == [
                f"{ticker} relevance requires review"
            ]
            assert review["decision_reasons"]["topic"] == [
                f"{ticker} has no reviewable topic cluster"
            ]

        expected_totals = {
            "input_article_count": None,
            **{
                key: sum(review["review_counts"][key] for review in data["reviews"])
                for key in data["reviews"][0]["review_counts"]
                if key != "input_article_count"
            },
        }
        assert data["review_counts"] == expected_totals

    def test_normalized_review_preserves_explicit_ticker_association_state(self):
        """Explicit producer association diagnostics are preserved without inference."""
        from backend.routers.stocks import _normalize_ticker_review

        payload = self._producer_payload("AAPL", 0)
        payload["run_readiness"]["ticker_association_state"] = {
            "state": "PASS",
            "reason": "Producer verified requested-ticker association.",
            "matched_article_count": 4,
        }
        payload["summary"]["input_article_count"] = 7

        review = _normalize_ticker_review(
            payload,
            ticker="AAPL",
            run_id="packet-1",
            from_date="2026-06-18",
            to_date="2026-06-18",
        )

        assert review["states"]["ticker_association"] == {
            "state": "PASS",
            "reason": "Producer verified requested-ticker association.",
            "matched_article_count": 4,
        }
        assert review["decision_reasons"]["ticker_association"] == [
            "Producer verified requested-ticker association."
        ]
        assert review["review_counts"]["input_article_count"] == 7

    def test_audit_keeps_successful_reviews_when_one_ticker_fails(self):
        """One producer error is isolated without dropping successful ticker reviews."""
        tickers = ["AAPL", "AMD", "NVDA", "MSFT"]
        payloads = {
            ticker: self._producer_payload(ticker, index)
            for index, ticker in enumerate(tickers)
        }

        def build_payload(_run_id, _from_date, _to_date, ticker):
            if ticker == "AMD":
                raise RuntimeError("AMD producer failure")
            return payloads[ticker]

        with (
            patch(
                "backend.routers.stocks._load_packet_catalog",
                return_value=self._catalog(),
            ),
            patch("backend.routers.stocks._build_review_options", return_value={}),
            patch("backend.routers.stocks._build_audit_payload", side_effect=build_payload),
        ):
            response = client.get(
                "/api/stocks/audit",
                params={"run_id": "packet-1", "tickers": ",".join(tickers)},
            )

        data = response.json()
        assert data["ok"] is False
        assert data["status"] == "fail"
        assert set(data["payload"]["tickers"]) == {"AAPL", "NVDA", "MSFT"}
        assert data["ticker_errors"] == {
            "AMD": {
                "error": "PacketContractError",
                "reason": "Producer audit payload could not be built.",
            }
        }
        assert [review["ticker"] for review in data["reviews"]] == [
            "AAPL",
            "AMD",
            "NVDA",
            "MSFT",
        ]
        assert data["review_counts"] == {
            key: None for key in data["reviews"][0]["review_counts"]
        }
        assert "AMD producer failure" not in json.dumps(data)


class TestAuthoritativePacketAdapter:
    """Exercise metadata discovery and fail-closed evidence validation."""

    @staticmethod
    def _packet(
        *,
        tickers: tuple[str, ...] = ("AAPL", "AMD", "NVDA", "MSFT"),
        smoke_status: str = "pass",
        zero_ticker: str | None = None,
        omit_sentence_count: bool = False,
    ) -> dict:
        """Return an authoritative packet-metadata fixture."""
        ticker_audits = {}
        for index, ticker in enumerate(tickers, start=1):
            article_count = 0 if ticker == zero_ticker else index
            row_count = 0 if ticker == zero_ticker else index + 10
            relevance_count = 0 if ticker == zero_ticker else index + 20
            summary = {
                "article_count": article_count,
                "row_count": row_count,
                "relevance_gate_row_count": relevance_count,
            }
            if omit_sentence_count:
                summary.pop("row_count")
            ticker_audits[ticker] = {
                "cached_fallback_used": False,
                "ticker_isolation_pass": True,
                "ticker_sets": {"article_groups": [ticker]},
                "summary": summary,
                "relevance": {
                    "gate_decisions": {
                        "accepted": article_count,
                        "borderline": 0,
                        "rejected": max(relevance_count - article_count, 0),
                    },
                    "included_row_count": row_count,
                },
                "artifact_keys": {
                    "news_sentiment_scored": [
                        f"features/2026-09-04/news_sentiment_scored/{ticker}.parquet"
                    ]
                },
                "topics": {
                    "diversity_status": "insufficient_diversity",
                    "diversity_reason": "No non-outlier topics were detected.",
                    "topic_row_count": article_count,
                },
                "api": {
                    "smoke_status": smoke_status,
                    "smoke_failures": (
                        [{"reason": "producer_smoke_failed"}]
                        if smoke_status == "fail"
                        else []
                    ),
                    "run_readiness": {
                        "readiness_status": "reviewable_with_warnings",
                        "smoke_status": smoke_status,
                    },
                },
            }
        return {
            "run_id": "issue281-post-pr306-current-prod-20260904-v1",
            "review_date": "2026-09-04",
            "tickers": list(tickers),
            "ticker_audits": ticker_audits,
            "manifests": [{"status": "completed"}],
        }

    @staticmethod
    def _write_packet(root: Path, packet: dict) -> None:
        """Write one packet fixture to a temporary diagnostics catalog."""
        root.mkdir(parents=True, exist_ok=True)
        (root / "packet_audit.json").write_text(json.dumps(packet), encoding="utf-8")

    def test_review_options_uses_packet_metadata_not_feature_directories(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Named run IDs, dates, and tickers come from authoritative JSON contents."""
        diagnostics = tmp_path / "diagnostics"
        self._write_packet(diagnostics, self._packet())
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        response = client.get("/api/stocks/review-options")

        candidate = {
            "id": "issue281-post-pr306-current-prod-20260904-v1",
            "label": "AAPL, AMD, NVDA, MSFT · 2026-09-04",
            "from_date": "2026-09-04",
            "to_date": "2026-09-04",
            "tickers": ["AAPL", "AMD", "NVDA", "MSFT"],
            "run_id": "issue281-post-pr306-current-prod-20260904-v1",
        }
        assert response.json() == {
            "ok": True,
            "candidates": [candidate],
            "default_selection": candidate,
        }

        audit = client.get(
            "/api/stocks/audit",
            params={"run_id": "issue281-post-pr306-current-prod-20260904-v1"},
        ).json()
        assert audit["query"]["tickers"] == ["AAPL", "AMD", "NVDA", "MSFT"]

    def test_audit_success_requires_all_requested_tickers(self, tmp_path, monkeypatch):
        """Four valid ticker records produce four correlated normalized reviews."""
        diagnostics = tmp_path / "diagnostics"
        self._write_packet(diagnostics, self._packet())
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        data = client.get(
            "/api/stocks/audit",
            params={
                "run_id": "issue281-post-pr306-current-prod-20260904-v1",
                "from_date": "2026-09-04",
                "to_date": "2026-09-04",
                "tickers": "AAPL,AMD,NVDA,MSFT",
            },
        ).json()

        assert data["ok"] is True
        assert data["status"] == "pass"
        assert [review["ticker"] for review in data["reviews"]] == [
            "AAPL", "AMD", "NVDA", "MSFT"
        ]
        assert data["review_counts"]["reviewed_article_count"] == 10
        assert data["ticker_errors"] == {}
        assert all(
            review["states"]["finbert"]["status"] == "available"
            for review in data["reviews"]
        )

    def test_zero_evidence_fails_closed(self, tmp_path, monkeypatch):
        """A canonical zero article/row record cannot produce ok=true."""
        diagnostics = tmp_path / "diagnostics"
        self._write_packet(diagnostics, self._packet(zero_ticker="AMD"))
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        data = client.get(
            "/api/stocks/audit",
            params={
                "run_id": "issue281-post-pr306-current-prod-20260904-v1",
                "tickers": "AAPL,AMD,NVDA,MSFT",
            },
        ).json()

        assert data["ok"] is False
        assert data["status"] == "fail"
        assert data["error"] == "SemanticEvidenceUnavailable"
        assert data["ticker_errors"]["AMD"]["error"] == "SemanticEvidenceUnavailable"
        assert "zero canonical article evidence" in data["ticker_errors"]["AMD"]["reason"]

    def test_partial_packet_fails_with_diagnostics_for_every_requested_ticker(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A missing ticker keeps all requested identities but is never success-shaped."""
        diagnostics = tmp_path / "diagnostics"
        self._write_packet(diagnostics, self._packet(tickers=("AAPL", "NVDA", "MSFT")))
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        data = client.get(
            "/api/stocks/audit",
            params={
                "run_id": "issue281-post-pr306-current-prod-20260904-v1",
                "tickers": "AAPL,AMD,NVDA,MSFT",
            },
        ).json()

        assert data["ok"] is False
        assert data["error"] == "SemanticEvidenceUnavailable"
        assert [review["ticker"] for review in data["reviews"]] == [
            "AAPL", "AMD", "NVDA", "MSFT"
        ]
        assert data["ticker_errors"]["AMD"] == {
            "error": "PacketContractError",
            "reason": "Requested ticker is not present in the selected packet.",
        }

    def test_failed_producer_smoke_fails_closed(self, tmp_path, monkeypatch):
        """A producer smoke failure is visible and prevents aggregate success."""
        diagnostics = tmp_path / "diagnostics"
        self._write_packet(diagnostics, self._packet(smoke_status="fail"))
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        data = client.get(
            "/api/stocks/audit",
            params={
                "run_id": "issue281-post-pr306-current-prod-20260904-v1",
                "tickers": "AAPL",
            },
        ).json()

        assert data["ok"] is False
        assert data["reviews"][0]["smoke_status"] == "fail"
        assert "producer smoke status is fail" in data["ticker_errors"]["AAPL"]["reason"]

    def test_cached_fallback_evidence_fails_closed(self, tmp_path, monkeypatch):
        """Cached fallback evidence remains visible but cannot produce ok=true."""
        diagnostics = tmp_path / "diagnostics"
        packet = self._packet()
        packet["ticker_audits"]["AAPL"]["cached_fallback_used"] = True
        self._write_packet(diagnostics, packet)
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        data = client.get(
            "/api/stocks/audit",
            params={
                "run_id": "issue281-post-pr306-current-prod-20260904-v1",
                "tickers": "AAPL",
            },
        ).json()

        assert data["ok"] is False
        assert data["reviews"][0]["cached_fallback_used"] is True
        assert "cached fallback evidence was used" in data["ticker_errors"]["AAPL"]["reason"]

    def test_exact_date_mismatch_is_not_substituted(self, tmp_path, monkeypatch):
        """A request outside packet metadata returns a controlled contract error."""
        diagnostics = tmp_path / "diagnostics"
        self._write_packet(diagnostics, self._packet())
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        data = client.get(
            "/api/stocks/audit",
            params={
                "run_id": "issue281-post-pr306-current-prod-20260904-v1",
                "from_date": "2026-09-05",
                "to_date": "2026-09-05",
                "tickers": "AAPL",
            },
        ).json()

        assert data["ok"] is False
        assert data["error"] == "PacketContractError"
        assert data["query"]["from_date"] == "2026-09-05"
        assert data["query"]["to_date"] == "2026-09-05"

    def test_missing_count_is_null_with_reason(self, tmp_path, monkeypatch):
        """An unexposed producer count remains JSON null with explicit provenance."""
        diagnostics = tmp_path / "diagnostics"
        self._write_packet(diagnostics, self._packet(omit_sentence_count=True))
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        data = client.get(
            "/api/stocks/audit",
            params={
                "run_id": "issue281-post-pr306-current-prod-20260904-v1",
                "tickers": "AAPL",
            },
        ).json()

        review = data["reviews"][0]
        assert review["review_counts"]["sentence_row_count"] is None
        assert review["review_count_reasons"]["sentence_row_count"] == (
            "not_exposed_by_producer"
        )

    def test_corrupt_metadata_returns_controlled_integrity_error(self, tmp_path, monkeypatch):
        """Unreadable candidate metadata fails closed without leaking parser internals."""
        diagnostics = tmp_path / "diagnostics"
        diagnostics.mkdir()
        (diagnostics / "broken_audit.json").write_text("{not-json", encoding="utf-8")
        monkeypatch.setattr(stocks, "DIAGNOSTICS_ROOT", diagnostics)

        data = client.get("/api/stocks/review-options").json()

        assert data["ok"] is False
        assert data["error"] == "PacketIntegrityError"
        assert data["reason"] == "Packet metadata could not be loaded."
