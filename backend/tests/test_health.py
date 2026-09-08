"""Health check tests (Phase 1 acceptance)."""

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "service" in payload
    assert "environment" in payload


def test_root_redirects_to_cases() -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code in {302, 307}
    assert "/cases" in response.headers.get("location", "")


def test_openapi_available() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    payload = response.json()
    assert payload["info"]["title"]
