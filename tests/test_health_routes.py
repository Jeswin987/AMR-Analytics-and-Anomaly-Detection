# tests/test_health_routes.py
"""Route-level tests for the liveness and readiness endpoints."""


def test_health_returns_alive(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "alive"}
