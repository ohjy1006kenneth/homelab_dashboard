from __future__ import annotations

import json
from contextlib import contextmanager

from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import stocks


class FakeHeaders(dict):
    def get_content_type(self):
        return self.get("content-type", "application/json").split(";", 1)[0]


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, content_type: str = "application/json"):
        self.body = body
        self.status = status
        self.headers = FakeHeaders({"content-type": content_type})

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.body


def test_review_proxies_exact_identity_and_headers(monkeypatch):
    calls = []
    body = json.dumps({"controls": {"ticker": "AAPL"}}).encode()

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return FakeResponse(body)

    monkeypatch.setattr(stocks, "urlopen", fake_urlopen)
    response = TestClient(app).get("/api/stocks/review?run_id=r&from_date=2026-06-18&to_date=2026-06-18&ticker=aapl")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert "ticker=AAPL" in calls[0][0]
    assert "run_id=r" in calls[0][0]
    assert calls[0][1] == stocks.TIMEOUT_SECONDS


def test_review_preserves_allowed_upstream_error(monkeypatch):
    monkeypatch.setattr(stocks, "urlopen", lambda *_args, **_kwargs: FakeResponse(b'{"error":"missing"}', 404))
    response = TestClient(app).get("/api/stocks/review?run_id=r&from_date=f&to_date=t&ticker=AAPL")
    assert response.status_code == 404
    assert response.json() == {"error": "missing"}


def test_review_rejects_invalid_json_and_over_budget(monkeypatch):
    monkeypatch.setattr(stocks, "urlopen", lambda *_args, **_kwargs: FakeResponse(b"not-json"))
    response = TestClient(app).get("/api/stocks/review?run_id=r&from_date=f&to_date=t&ticker=AAPL")
    assert response.status_code == 502
    assert "invalid UTF-8 JSON" in response.json()["error"]

    monkeypatch.setattr(stocks, "urlopen", lambda *_args, **_kwargs: FakeResponse(b" " * (stocks.MAX_BODY_BYTES + 1)))
    response = TestClient(app).get("/api/stocks/review?run_id=r&from_date=f&to_date=t&ticker=AAPL")
    assert response.status_code == 502
    assert "200000-byte" in response.json()["error"]


def test_review_accepts_199999_bytes_but_rejects_exact_budget(monkeypatch):
    overhead = len(json.dumps({"padding": ""}).encode("utf-8"))
    valid_body = json.dumps({"padding": "x" * (199999 - overhead)}).encode("utf-8")
    assert len(valid_body) == 199999
    monkeypatch.setattr(stocks, "urlopen", lambda *_args, **_kwargs: FakeResponse(valid_body))
    response = TestClient(app).get("/api/stocks/review?run_id=r&from_date=f&to_date=t&ticker=AAPL")
    assert response.status_code == 200

    exact_body = json.dumps({"padding": "x" * (200000 - overhead)}).encode("utf-8")
    assert len(exact_body) == 200000
    monkeypatch.setattr(stocks, "urlopen", lambda *_args, **_kwargs: FakeResponse(exact_body))
    response = TestClient(app).get("/api/stocks/review?run_id=r&from_date=f&to_date=t&ticker=AAPL")
    assert response.status_code == 502


def test_review_requires_all_identity_fields():
    response = TestClient(app).get("/api/stocks/review?ticker=AAPL")
    assert response.status_code == 422


def test_review_rejects_unsupported_upstream_status(monkeypatch):
    monkeypatch.setattr(stocks, "urlopen", lambda *_args, **_kwargs: FakeResponse(b'{"error":"no"}', 500))
    response = TestClient(app).get("/api/stocks/review?run_id=r&from_date=f&to_date=t&ticker=AAPL")
    assert response.status_code == 502
    assert "unsupported upstream status" in response.json()["error"]
