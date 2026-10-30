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
    check_influx,
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


# --------------------------------------------------------------------------
# InfluxDB probe
# --------------------------------------------------------------------------

class _FakeBucketsApi:
    def __init__(self, bucket=None, raises=None):
        self._bucket = bucket
        self._raises = raises

    def find_bucket_by_name(self, name):
        if self._raises is not None:
            raise self._raises
        return self._bucket


class _FakeInfluxClient:
    """Stands in for InfluxDBClient, recording close() calls."""

    def __init__(self, ping=True, bucket=None, bucket_raises=None):
        self._ping = ping
        self._buckets = _FakeBucketsApi(bucket=bucket, raises=bucket_raises)
        self.closed = False

    def ping(self):
        return self._ping

    def buckets_api(self):
        return self._buckets

    def close(self):
        self.closed = True


def _patch_influx_client(monkeypatch, client, recorder=None):
    def fake_client(**kwargs):
        if recorder is not None:
            recorder.update(kwargs)
        return client

    monkeypatch.setattr(health_service, "InfluxDBClient", fake_client)
    return client


def test_check_influx_up_when_ping_and_bucket_succeed(monkeypatch):
    _patch_influx_client(monkeypatch, _FakeInfluxClient(ping=True, bucket=object()))

    result = check_influx()

    assert result["status"] == "up"
    assert result["latency_ms"] >= 0


def test_check_influx_unreachable_when_ping_false(monkeypatch):
    _patch_influx_client(monkeypatch, _FakeInfluxClient(ping=False))

    result = check_influx()

    assert result == {"status": "down", "reason": REASON_UNREACHABLE}


def test_check_influx_unhealthy_when_bucket_missing(monkeypatch):
    _patch_influx_client(monkeypatch, _FakeInfluxClient(ping=True, bucket=None))

    result = check_influx()

    assert result == {"status": "down", "reason": REASON_UNHEALTHY}


def test_check_influx_unhealthy_when_token_rejected(monkeypatch):
    rejected = Exception("(401) Reason: unauthorized access")
    _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket_raises=rejected)
    )

    result = check_influx()

    assert result == {"status": "down", "reason": REASON_UNHEALTHY}


def test_check_influx_unhealthy_with_empty_token(monkeypatch):
    """Config defaults INFLUX_TOKEN to "", so an unconfigured install must
    never be reported as up: /ping is unauthenticated and would say otherwise."""
    monkeypatch.setitem(health_service.Config.INFLUXDB, "token", "")
    rejected = Exception("(401) Reason: unauthorized access")
    _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket_raises=rejected)
    )

    result = check_influx()

    assert result["status"] != "up"
    assert result["reason"] == REASON_UNHEALTHY


def test_check_influx_closes_client_on_failure(monkeypatch):
    client = _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket_raises=Exception("nope"))
    )

    check_influx()

    assert client.closed is True


def test_check_influx_passes_timeout_in_milliseconds(monkeypatch):
    """The influxdb-client timeout parameter is milliseconds, not seconds."""
    recorder = {}
    _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket=object()), recorder=recorder
    )

    check_influx(timeout_s=2.0)

    assert recorder["timeout"] == 2000
