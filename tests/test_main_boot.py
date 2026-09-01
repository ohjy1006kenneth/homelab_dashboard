from fastapi.testclient import TestClient

from backend.main import app


def test_main_imports_with_all_registered_routers_and_static_mount():
    client = TestClient(app)

    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/").status_code == 200
    assert client.get("/static/favicon.svg?v=20260602").status_code == 200

    paths = client.get("/openapi.json").json()["paths"]
    for prefix in ("/api/metrics", "/api/apps", "/api/agents", "/api/newsletters", "/api/overview", "/api/settings"):
        assert any(path == prefix or path.startswith(f"{prefix}/") for path in paths)