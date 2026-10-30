# tests/test_health_routes.py
"""Route-level tests for the liveness and readiness endpoints."""
import pytest

from backend.services import health_service

_UP = {"status": "up", "latency_ms": 3}


def _patch_probes(monkeypatch, postgres=None, influx=None):
    monkeypatch.setattr(health_service, "check_postgres", lambda *a, **k: postgres)
    monkeypatch.setattr(health_service, "check_influx", lambda *a, **k: influx)


def test_health_returns_alive(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "alive"}


def test_health_performs_no_dependency_io(client, monkeypatch):
    """
    REGRESSION GUARD, not a TDD driver.

    Liveness must stay cheap: a monitor may poll it every second. This test
    passed the moment it was written, because the handler has never probed a
    dependency -- there is no revision of this code against which it goes red
    on its own. Its teeth were verified by hand instead: adding a
    check_postgres() call to the handler makes it fail with
    "liveness probed a dependency", and removing it makes it pass again. It
    exists so that someone cannot quietly make liveness expensive later.
    """

    def explode(*a, **k):
        raise AssertionError("liveness probed a dependency")

    monkeypatch.setattr(health_service, "check_postgres", explode)
    monkeypatch.setattr(health_service, "check_influx", explode)

    resp = client.get("/api/health")

    assert resp.status_code == 200


def test_ready_200_when_all_up(client, monkeypatch):
    _patch_probes(monkeypatch, postgres=_UP, influx=_UP)

    resp = client.get("/api/ready")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["checks"]["postgresql"]["status"] == "up"
    assert body["checks"]["influxdb"]["status"] == "up"


def test_ready_503_when_any_down(client, monkeypatch):
    _patch_probes(
        monkeypatch, postgres=_UP, influx={"status": "down", "reason": "unhealthy"}
    )

    resp = client.get("/api/ready")

    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "not_ready"
    assert body["checks"]["influxdb"]["reason"] == "unhealthy"


def test_ready_body_leaks_no_server_text(client, monkeypatch):
    """
    The probes sanitise their own failures, so a raising dependency must not
    put any of this into the response. Asserted against the raw bytes rather
    than a parsed dict, so a leak nested anywhere still trips it.
    """
    secret = "FATAL: password=hunter2 host=10.0.0.5 token=abc123"

    def raising_engine(*a, **k):
        raise RuntimeError(secret)

    monkeypatch.setattr(health_service, "create_engine", raising_engine)
    monkeypatch.setattr(health_service, "check_influx", lambda *a, **k: _UP)

    resp = client.get("/api/ready")
    body = resp.data.decode()

    assert resp.status_code == 503
    for leaked in ("hunter2", "10.0.0.5", "abc123", "FATAL", "password"):
        assert leaked not in body
