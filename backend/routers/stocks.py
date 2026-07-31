from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Query
from fastapi.responses import Response

router = APIRouter(prefix="/api/stocks", tags=["stocks"])
MAX_BODY_BYTES = 200_000
TIMEOUT_SECONDS = 8


def _response(body: bytes, status_code: int) -> Response:
    return Response(
        content=body,
        status_code=status_code,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "Content-Type": "application/json; charset=utf-8"},
    )


def _gateway_error(message: str) -> Response:
    body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
    return _response(body, 502)


@router.get("/review")
def review(
    run_id: str = Query(..., min_length=1),
    from_date: str = Query(..., min_length=1),
    to_date: str = Query(..., min_length=1),
    ticker: str = Query(..., min_length=1),
) -> Response:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        return _response(b'{"error":"ticker is required"}', 400)
    query = urlencode({"run_id": run_id, "from_date": from_date, "to_date": to_date, "ticker": normalized_ticker})
    base = os.environ.get("STOCK_REVIEW_API_URL", "http://127.0.0.1:8766").rstrip("/")
    request = UrlRequest(
        f"{base}/api/review?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as upstream:
            status = int(upstream.status)
            body = upstream.read(MAX_BODY_BYTES + 1)
            content_type = upstream.headers.get_content_type()
    except HTTPError as error:
        status = int(error.code)
        body = error.read(MAX_BODY_BYTES + 1)
        content_type = error.headers.get_content_type() if error.headers else "application/json"
    except (URLError, TimeoutError, OSError):
        return _gateway_error("Stock review gateway could not reach the evidence service.")

    if len(body) >= MAX_BODY_BYTES:
        return _gateway_error("Stock review response exceeded the 200000-byte gateway budget.")
    if status not in {200, 400, 404}:
        return _gateway_error("Stock review gateway received an unsupported upstream status.")
    if content_type != "application/json":
        return _gateway_error("Stock review gateway received a non-JSON response.")
    try:
        json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _gateway_error("Stock review gateway received invalid UTF-8 JSON.")
    return _response(body, status)
