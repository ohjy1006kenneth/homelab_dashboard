"""Proxy routes for AI-Stock-Trader semantic review dashboard."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter()

TRADER_ROOT = Path("/home/juyoungoh/AI-Stock-Trader")
DIAGNOSTICS_ROOT = TRADER_ROOT / "artifacts" / "reports" / "diagnostics"
REVIEW_COUNT_KEYS = (
    "input_article_count",
    "sentence_row_count",
    "reviewed_article_count",
    "relevance_row_count",
    "accepted_decision_count",
    "borderline_decision_count",
    "rejected_decision_count",
)


class PacketNotFoundError(Exception):
    """Requested run_id does not exist or was not provided."""


class PacketIntegrityError(Exception):
    """Packet metadata cannot be discovered or is corrupt."""


class PacketContractError(Exception):
    """Packet contents violate the expected schema."""


def _packet_score(packet: dict[str, Any]) -> tuple[int, int]:
    """Rank duplicate metadata artifacts by explicit evidence completeness."""
    ticker_audits = packet.get("ticker_audits")
    if not isinstance(ticker_audits, dict):
        return (0, 0)
    explicit_isolation = sum(
        isinstance(audit, dict) and "ticker_isolation_pass" in audit
        for audit in ticker_audits.values()
    )
    field_count = sum(len(audit) for audit in ticker_audits.values() if isinstance(audit, dict))
    return explicit_isolation, field_count


def _load_packet_catalog() -> dict[str, dict[str, Any]]:
    """Load authoritative review packet metadata from diagnostic artifacts."""
    if not DIAGNOSTICS_ROOT.is_dir():
        return {}

    catalog: dict[str, dict[str, Any]] = {}
    for path in sorted(DIAGNOSTICS_ROOT.glob("*audit*.json")):
        try:
            packet = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PacketIntegrityError("Packet metadata could not be loaded.") from exc
        if not isinstance(packet, dict) or "run_id" not in packet:
            continue

        run_id = packet.get("run_id")
        review_date = packet.get("review_date")
        tickers = packet.get("tickers")
        ticker_audits = packet.get("ticker_audits")
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(review_date, str)
            or not isinstance(tickers, list)
            or not all(isinstance(ticker, str) and ticker for ticker in tickers)
            or not isinstance(ticker_audits, dict)
        ):
            raise PacketIntegrityError("Packet metadata violates the expected contract.")
        try:
            date.fromisoformat(review_date)
        except ValueError as exc:
            raise PacketIntegrityError("Packet metadata contains an invalid review date.") from exc

        record = {"packet": packet, "source": path, "score": _packet_score(packet)}
        previous = catalog.get(run_id)
        if previous is None or record["score"] > previous["score"]:
            catalog[run_id] = record
    return catalog


def _find_run_ids() -> list[str]:
    """Return actual packet run IDs discovered from authoritative metadata."""
    return list(_load_packet_catalog())


def _build_audit_payload(
    run_id: str,
    from_date: str,
    to_date: str,
    ticker: str,
) -> dict[str, Any]:
    """Adapt one exact packet ticker audit without substituting another run."""
    record = _load_packet_catalog().get(run_id)
    if record is None:
        raise PacketNotFoundError("Selected packet was not found.")
    packet = _mapping(record.get("packet"))
    review_date = packet.get("review_date")
    if from_date != review_date or to_date != review_date:
        raise PacketContractError("Requested dates do not match the selected packet.")

    packet_tickers = packet.get("tickers")
    ticker_audits = _mapping(packet.get("ticker_audits"))
    if not isinstance(packet_tickers, list) or ticker not in packet_tickers:
        raise PacketContractError("Requested ticker is not present in the selected packet.")
    ticker_audit = _mapping(ticker_audits.get(ticker))
    if not ticker_audit:
        raise PacketContractError("Requested ticker evidence is missing from the selected packet.")

    raw_summary = _mapping(ticker_audit.get("summary"))
    summary = _mapping(raw_summary.get("canonical_summary")) or raw_summary
    api = _mapping(ticker_audit.get("api"))
    readiness = _mapping(api.get("run_readiness"))
    relevance = _mapping(ticker_audit.get("relevance")) or _mapping(
        ticker_audit.get("relevance_gate")
    )
    raw_finbert = ticker_audit.get("scored_rows")
    finbert = _mapping(raw_finbert)
    if isinstance(raw_finbert, int) and not isinstance(raw_finbert, bool):
        finbert = {"row_count": raw_finbert}
    if not finbert:
        scored_artifact = _mapping(ticker_audit.get("artifact_keys")).get(
            "news_sentiment_scored"
        )
        artifact_keys = (
            [scored_artifact]
            if isinstance(scored_artifact, str) and scored_artifact
            else scored_artifact
        )
        if (
            isinstance(artifact_keys, list)
            and artifact_keys
            and all(isinstance(value, str) and value for value in artifact_keys)
        ):
            finbert = {
                "status": "available",
                "artifact_keys": artifact_keys,
                "row_count": summary.get("row_count"),
            }
    topic = _mapping(ticker_audit.get("topics")) or _mapping(
        ticker_audit.get("topic_review")
    )
    if "reason" not in topic and isinstance(topic.get("diversity_reason"), str):
        topic = {**topic, "reason": topic["diversity_reason"]}
    isolation_value = ticker_audit.get("ticker_isolation_pass")
    association_state = {
        "status": "available" if isinstance(isolation_value, bool) else "not_available",
        "ticker_isolation_pass": isolation_value if isinstance(isolation_value, bool) else None,
        "ticker_sets": _mapping(ticker_audit.get("ticker_sets")),
        "reason": None if isinstance(isolation_value, bool) else "not_exposed_by_producer",
    }
    return {
        "controls": {
            "run_id": run_id,
            "from_date": review_date,
            "to_date": review_date,
            "ticker": ticker,
        },
        "summary": summary,
        "run_readiness": readiness,
        "smoke": {
            "status": api.get("smoke_status") or readiness.get("smoke_status"),
            "failures": list(api.get("smoke_failures") or []),
        },
        "ticker_association_state": association_state,
        "diagnostic_sections": {
            "relevance": relevance,
            "finbert": finbert,
            "topic": topic,
        },
        "cached_fallback_used": ticker_audit.get("cached_fallback_used"),
    }


def _mapping(value: Any) -> dict[str, Any]:
    """Return a shallow mapping copy or an empty mapping for non-mappings."""
    return dict(value) if isinstance(value, dict) else {}


def _optional_count(*values: Any) -> int | None:
    """Return the first canonical non-negative integer count, or unknown when absent."""
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _gate_state(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Return the producer gate state for one canonical pipeline stage."""
    gate_cards = payload.get("gate_cards")
    if not isinstance(gate_cards, list):
        return {"status": None, "message": None, "failure_reasons": []}
    for candidate in gate_cards:
        if isinstance(candidate, dict) and candidate.get("key") == key:
            return {
                "status": candidate.get("status"),
                "message": candidate.get("message"),
                "failure_reasons": list(candidate.get("failure_reasons") or []),
            }
    return {"status": None, "message": None, "failure_reasons": []}


def _ticker_association_state(
    payload: dict[str, Any],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    """Return only explicit producer ticker-association diagnostics."""
    for source in (readiness, payload):
        state = _mapping(source.get("ticker_association_state"))
        if state:
            return state

    association_gate = _gate_state(payload, "ticker_association")
    if association_gate.get("status") is not None:
        return association_gate

    return {
        "state": "unknown",
        "status": "not_available",
        "reason": "not_exposed_by_producer",
    }


def _state_with_gate(
    producer_state: dict[str, Any],
    gate_state: dict[str, Any],
) -> dict[str, Any]:
    """Preserve a producer diagnostic state alongside its matching gate state."""
    return {
        **producer_state,
        "gate_status": gate_state.get("status"),
        "gate_message": gate_state.get("message"),
        "gate_failure_reasons": list(gate_state.get("failure_reasons") or []),
    }


def _decision_reasons(
    producer_state: dict[str, Any],
    gate_state: dict[str, Any],
) -> list[str]:
    """Return canonical producer reasons without deriving new decision semantics."""
    state_reason = producer_state.get("reason")
    if isinstance(state_reason, str) and state_reason:
        return [state_reason]
    gate_message = gate_state.get("message")
    if isinstance(gate_message, str) and gate_message:
        return [gate_message]
    return [
        reason
        for reason in gate_state.get("failure_reasons") or []
        if isinstance(reason, str) and reason
    ]


def _normalize_ticker_review(
    payload: dict[str, Any],
    *,
    ticker: str,
    run_id: str,
    from_date: str,
    to_date: str,
) -> dict[str, Any]:
    """Adapt one producer payload to the stable multi-ticker review contract."""
    summary = _mapping(payload.get("summary"))
    readiness = _mapping(payload.get("run_readiness"))
    smoke = _mapping(payload.get("smoke"))
    controls = _mapping(payload.get("controls"))
    diagnostic_sections = _mapping(payload.get("diagnostic_sections"))
    topic_relevance = _mapping(payload.get("topic_relevance_review"))
    topic_relevance_summary = _mapping(topic_relevance.get("summary"))
    relevance_state = _mapping(readiness.get("relevance_informativeness_state"))
    if not relevance_state:
        relevance_state = _mapping(topic_relevance_summary.get("relevance_informativeness_state"))
    diagnostic_relevance = _mapping(diagnostic_sections.get("relevance"))
    if not relevance_state and diagnostic_relevance:
        relevance_state = {"status": "available", **diagnostic_relevance}
    topic_state = _mapping(readiness.get("topic_review_state"))
    if not topic_state:
        topic_state = _mapping(topic_relevance_summary.get("topic_review_state"))
    diagnostic_topic = _mapping(diagnostic_sections.get("topic"))
    if not topic_state and diagnostic_topic:
        topic_state = {"status": "available", **diagnostic_topic}
    diagnostic_finbert = _mapping(diagnostic_sections.get("finbert"))

    association_state = _ticker_association_state(payload, readiness)
    relevance_gate = _gate_state(payload, "news_relevance_gate")
    finbert_gate = _gate_state(payload, "news_sentiment_scored")
    if diagnostic_finbert:
        finbert_gate = {"status": "available", **diagnostic_finbert}
    topic_gate = _gate_state(payload, "topic_labels")
    gate_decisions = _mapping(diagnostic_relevance.get("gate_decisions"))
    review_counts = {
        "input_article_count": _optional_count(
            summary.get("input_article_count"),
            readiness.get("input_article_count"),
        ),
        "sentence_row_count": _optional_count(
            summary.get("row_count"), readiness.get("sentence_row_count")
        ),
        "reviewed_article_count": _optional_count(
            summary.get("article_count"), readiness.get("article_count")
        ),
        "relevance_row_count": _optional_count(
            summary.get("relevance_gate_row_count"),
            relevance_state.get("relevance_gate_row_count"),
            diagnostic_relevance.get("total_rows"),
        ),
        "accepted_decision_count": _optional_count(
            relevance_state.get("accepted_count"),
            topic_relevance_summary.get("accepted_count"),
            gate_decisions.get("accepted"),
        ),
        "borderline_decision_count": _optional_count(
            relevance_state.get("borderline_count"),
            topic_relevance_summary.get("borderline_count"),
            gate_decisions.get("borderline"),
        ),
        "rejected_decision_count": _optional_count(
            relevance_state.get("rejected_count"),
            topic_relevance_summary.get("rejected_count"),
            gate_decisions.get("rejected"),
        ),
    }
    review_count_reasons = {
        key: (None if value is not None else "not_exposed_by_producer")
        for key, value in review_counts.items()
    }
    return {
        "ticker": ticker,
        "run_id": run_id,
        "query": {"from_date": from_date, "to_date": to_date, "ticker": ticker},
        "producer_correlation": {
            "run_id": controls.get("run_id"),
            "from_date": controls.get("from_date"),
            "to_date": controls.get("to_date"),
            "ticker": controls.get("ticker"),
        },
        "status": readiness.get("readiness_status") or smoke.get("status") or "unknown",
        "smoke_status": readiness.get("smoke_status") or smoke.get("status") or "unknown",
        "cached_fallback_used": payload.get("cached_fallback_used"),
        "review_counts": review_counts,
        "review_count_reasons": review_count_reasons,
        "states": {
            "ticker_association": association_state,
            "relevance": _state_with_gate(relevance_state, relevance_gate),
            "finbert": finbert_gate,
            "topic": _state_with_gate(topic_state, topic_gate),
        },
        "decision_reasons": {
            "ticker_association": _decision_reasons(association_state, {}),
            "relevance": _decision_reasons(relevance_state, relevance_gate),
            "finbert": _decision_reasons({}, finbert_gate),
            "topic": _decision_reasons(topic_state, topic_gate),
        },
    }


def _unavailable_ticker_review(
    *,
    ticker: str,
    run_id: str,
    from_date: str,
    to_date: str,
    reason: str,
) -> dict[str, Any]:
    """Return bounded diagnostics for a requested ticker that could not be loaded."""
    return {
        "ticker": ticker,
        "run_id": run_id,
        "query": {"from_date": from_date, "to_date": to_date, "ticker": ticker},
        "producer_correlation": {
            "run_id": None,
            "from_date": None,
            "to_date": None,
            "ticker": None,
        },
        "status": "unavailable",
        "smoke_status": "unknown",
        "review_counts": {key: None for key in REVIEW_COUNT_KEYS},
        "review_count_reasons": {key: reason for key in REVIEW_COUNT_KEYS},
        "states": {
            key: {"status": "not_available", "reason": reason}
            for key in ("ticker_association", "relevance", "finbert", "topic")
        },
        "decision_reasons": {
            key: [reason] for key in ("ticker_association", "relevance", "finbert", "topic")
        },
    }


def _review_unavailability_reasons(review: dict[str, Any]) -> list[str]:
    """Validate correlation and minimum canonical evidence for one review."""
    reasons: list[str] = []
    query = _mapping(review.get("query"))
    correlation = _mapping(review.get("producer_correlation"))
    if any(correlation.get(key) != query.get(key) for key in ("from_date", "to_date", "ticker")):
        reasons.append("producer correlation does not match the exact request")
    if correlation.get("run_id") != review.get("run_id"):
        reasons.append("producer run_id does not match the selected packet")

    counts = _mapping(review.get("review_counts"))
    article_count = _optional_count(counts.get("reviewed_article_count"))
    relevance_count = _optional_count(counts.get("relevance_row_count"))
    if article_count is None:
        reasons.append("canonical article evidence is not exposed by the producer")
    elif article_count == 0:
        reasons.append("zero canonical article evidence")
    if relevance_count is None:
        reasons.append("ticker-association/relevance evidence is not exposed by the producer")
    elif relevance_count == 0:
        reasons.append("zero ticker-association/relevance evidence rows")

    association = _mapping(_mapping(review.get("states")).get("ticker_association"))
    explicit_association = association.get("ticker_isolation_pass") is True or str(
        association.get("state") or association.get("status") or ""
    ).lower() in {"pass", "passed", "ready", "verified"}
    if not explicit_association:
        reasons.append("explicit ticker-association evidence is unavailable or failed")

    if review.get("cached_fallback_used") is True:
        reasons.append("cached fallback evidence was used")

    smoke_status = str(review.get("smoke_status") or "").lower()
    if smoke_status in {"fail", "failed", "error", "blocked"}:
        reasons.append(f"producer smoke status is {smoke_status}")
    elif smoke_status in {"", "unknown", "not_available"}:
        reasons.append("producer smoke status is not exposed")
    return reasons


def _aggregate_review_counts(reviews: list[dict[str, Any]]) -> dict[str, int | None]:
    """Sum canonical counts only when every successful review reports that count."""
    aggregated: dict[str, int | None] = {}
    for key in REVIEW_COUNT_KEYS:
        values = [
            _optional_count(_mapping(review.get("review_counts")).get(key))
            for review in reviews
        ]
        aggregated[key] = (
            sum(value for value in values if value is not None)
            if all(value is not None for value in values)
            else None
        )
    return aggregated


def _extract_packet_dates(run_id: str) -> tuple[str, str]:
    """Return the exact bounded date range declared by packet metadata."""
    record = _load_packet_catalog().get(run_id)
    if record is None:
        raise PacketNotFoundError("Selected packet was not found.")
    packet = _mapping(record.get("packet"))
    review_date = packet.get("review_date")
    if not isinstance(review_date, str):
        raise PacketIntegrityError("Packet metadata does not declare a review date.")
    return review_date, review_date


def _build_review_options() -> dict[str, Any]:
    """Build review options (candidates + default) from available packet metadata."""
    catalog = _load_packet_catalog()
    if not catalog:
        return {"candidates": [], "default_selection": {}}

    candidates = []
    for run_id, record in catalog.items():
        packet = _mapping(record.get("packet"))
        review_date = packet["review_date"]
        packet_tickers = [
            ticker
            for ticker in packet["tickers"]
            if ticker in _mapping(packet.get("ticker_audits"))
        ]
        candidates.append(
            {
                "id": run_id,
                "label": f"{', '.join(packet_tickers)} · {review_date}",
                "from_date": review_date,
                "to_date": review_date,
                "tickers": packet_tickers,
                "run_id": run_id,
            }
        )

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
    except PacketIntegrityError:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketIntegrityError",
            "reason": "Packet metadata could not be loaded.",
            "candidates": [],
            "default_selection": {},
        }


@router.get("/api/stocks/audit")
def audit(
    run_id: str | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    tickers: str | None = Query(None),
):
    """Return the full semantic review audit payload for the specified packet."""
    tickers_list = list(
        dict.fromkeys(t.strip().upper() for t in (tickers or "").split(",") if t.strip())
    )
    query = {"from_date": from_date, "to_date": to_date, "tickers": tickers_list}

    if not run_id:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketNotFoundError",
            "reason": "run_id is required; select a packet from /api/stocks/review-options",
            "payload": {},
            "query": query,
        }

    try:
        catalog = _load_packet_catalog()
    except PacketIntegrityError:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketIntegrityError",
            "reason": "Packet metadata could not be loaded.",
            "run_id": run_id,
            "payload": {},
            "query": query,
        }

    record = catalog.get(run_id)
    if record is None:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketNotFoundError",
            "reason": "Selected packet was not found.",
            "run_id": run_id,
            "payload": {},
            "query": query,
        }

    packet = _mapping(record.get("packet"))
    if not tickers_list:
        tickers_list = list(packet["tickers"])
        query["tickers"] = tickers_list
    packet_date = packet.get("review_date")
    if not isinstance(packet_date, str):
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketIntegrityError",
            "reason": "Packet metadata does not declare a review date.",
            "run_id": run_id,
            "payload": {},
            "query": query,
        }
    if from_date is None:
        from_date = packet_date
    if to_date is None:
        to_date = packet_date
    query = {"from_date": from_date, "to_date": to_date, "tickers": tickers_list}
    if from_date != packet_date or to_date != packet_date:
        return {
            "ok": False,
            "status": "fail",
            "error": "PacketContractError",
            "reason": "Requested dates do not match the selected packet.",
            "run_id": run_id,
            "payload": {},
            "query": query,
        }

    results: dict[str, Any] = {}
    ticker_errors: dict[str, dict[str, str]] = {}
    reviews: list[dict[str, Any]] = []
    for ticker in tickers_list:
        try:
            ticker_payload = _build_audit_payload(run_id, from_date, to_date, ticker)
        except (PacketNotFoundError, PacketIntegrityError, PacketContractError) as exc:
            reason = str(exc)
            ticker_errors[ticker] = {"error": type(exc).__name__, "reason": reason}
            reviews.append(
                _unavailable_ticker_review(
                    ticker=ticker,
                    run_id=run_id,
                    from_date=from_date,
                    to_date=to_date,
                    reason=reason,
                )
            )
            continue
        except RuntimeError:
            reason = "Producer audit payload could not be built."
            ticker_errors[ticker] = {"error": "PacketContractError", "reason": reason}
            reviews.append(
                _unavailable_ticker_review(
                    ticker=ticker,
                    run_id=run_id,
                    from_date=from_date,
                    to_date=to_date,
                    reason=reason,
                )
            )
            continue

        results[ticker] = ticker_payload
        review = _normalize_ticker_review(
                ticker_payload,
                ticker=ticker,
                run_id=run_id,
                from_date=from_date,
                to_date=to_date,
            )
        reviews.append(review)
        reasons = _review_unavailability_reasons(review)
        if reasons:
            ticker_errors[ticker] = {
                "error": "SemanticEvidenceUnavailable",
                "reason": "; ".join(reasons),
            }

    ok = not ticker_errors and len(reviews) == len(tickers_list)
    response = {
        "ok": ok,
        "status": "pass" if ok else "fail",
        "run_id": run_id,
        "query": query,
        "payload": {
            "tickers": results,
            "errors": ticker_errors,
        },
        "ticker_errors": ticker_errors,
        "reviews": reviews,
        "review_options": _build_review_options(),
        "review_counts": _aggregate_review_counts(reviews),
    }
    if not ok:
        failed_tickers = ", ".join(ticker_errors)
        response.update(
            {
                "error": "SemanticEvidenceUnavailable",
                "reason": f"Evidence is unavailable for requested ticker(s): {failed_tickers}.",
            }
        )
    return response
