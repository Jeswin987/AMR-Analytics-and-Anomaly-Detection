# tests/test_health_service.py
"""
Unit tests for the readiness probes.

These exercise backend.services.health_service directly. That module imports
only Config and the two database libraries, so nothing here needs a Flask app
or a live dependency.
"""
import pytest

from backend.services import health_service
from backend.services.health_service import (
    REASON_TIMEOUT,
    REASON_UNHEALTHY,
    REASON_UNREACHABLE,
    check_postgres,
)


class _FakeConnection:
    def __init__(self, on_execute=None):
        self._on_execute = on_execute
        self.executed = []

    def execute(self, statement):
        if self._on_execute is not None:
            raise self._on_execute
        self.executed.append(statement)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeEngine:
    """Stands in for a SQLAlchemy Engine, recording dispose() calls."""

    def __init__(self, on_connect=None, on_execute=None):
        self._on_connect = on_connect
        self._on_execute = on_execute
        self.disposed = False

    def connect(self):
        if self._on_connect is not None:
            raise self._on_connect
        return _FakeConnection(self._on_execute)

    def dispose(self):
        self.disposed = True


def _patch_create_engine(monkeypatch, engine=None, raises=None, recorder=None):
    """Replace health_service.create_engine; optionally record its kwargs."""

    def fake_create_engine(url, **kwargs):
        if recorder is not None:
            recorder.update(kwargs)
        if raises is not None:
            raise raises
        return engine

    monkeypatch.setattr(health_service, "create_engine", fake_create_engine)


def test_check_postgres_up_on_successful_select(monkeypatch):
    engine = _FakeEngine()
    _patch_create_engine(monkeypatch, engine=engine)

    result = check_postgres()

    assert result["status"] == "up"
    assert result["latency_ms"] >= 0
    assert "reason" not in result


def test_check_postgres_down_when_engine_raises(monkeypatch):
    _patch_create_engine(monkeypatch, raises=OSError("connection refused"))

    result = check_postgres()

    assert result == {"status": "down", "reason": REASON_UNREACHABLE}


def test_check_postgres_never_raises(monkeypatch):
    _patch_create_engine(monkeypatch, raises=RuntimeError("boom"))

    result = check_postgres()

    assert result["status"] == "down"


def test_check_postgres_disposes_engine_on_failure(monkeypatch):
    engine = _FakeEngine(on_connect=OSError("connection refused"))
    _patch_create_engine(monkeypatch, engine=engine)

    check_postgres()

    assert engine.disposed is True


def test_connect_timeout_never_zero(monkeypatch):
    """int(0.4) is 0, which libpq reads as "wait forever" -- the opposite."""
    recorder = {}
    _patch_create_engine(monkeypatch, engine=_FakeEngine(), recorder=recorder)

    check_postgres(timeout_s=0.4)

    assert recorder["connect_args"]["connect_timeout"] >= 1
