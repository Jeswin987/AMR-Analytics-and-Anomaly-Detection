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
    check_readiness,
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


def test_check_influx_unhealthy_with_empty_token_without_any_io(monkeypatch):
    """
    Config defaults INFLUX_TOKEN to "", so an unconfigured install must never
    report up -- the server does not check the token on /ping and would say
    otherwise. An empty token is knowably wrong without a round trip, so this
    also asserts no client is constructed at all.
    """
    monkeypatch.setitem(health_service.Config.INFLUXDB, "token", "")

    def no_client_allowed(**kwargs):
        raise AssertionError("constructed a client for an empty token")

    monkeypatch.setattr(health_service, "InfluxDBClient", no_client_allowed)

    result = check_influx()

    assert result == {"status": "down", "reason": REASON_UNHEALTHY}


def test_check_influx_timeout_never_zero(monkeypatch):
    """
    urllib3 guards its timeout with a truthiness test, so a 0 falls through to
    "no timeout at all" -- the same trap as libpq connect_timeout=0. A probe
    with no timeout waits on the OS default (measured at 21s).
    """
    recorder = {}
    _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket=object()), recorder=recorder
    )

    check_influx(timeout_s=0.0)

    assert recorder["timeout"] >= 1


def test_check_influx_pins_retries_off(monkeypatch):
    """Already the library default; pinned so an upgrade cannot change it."""
    recorder = {}
    _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket=object()), recorder=recorder
    )

    check_influx()

    assert recorder["retries"] is False


def test_check_influx_divides_timeout_by_resolved_addresses(monkeypatch):
    """
    urllib3 applies its timeout per connection attempt and create_connection
    walks every resolved address, so a 2-address host would otherwise take
    2 x budget. Two calls share the budget, hence budget/2/addresses.
    """
    recorder = {}
    monkeypatch.setattr(health_service, "_address_count", lambda url: 2)
    _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket=object()), recorder=recorder
    )

    check_influx(timeout_s=2.0)

    assert recorder["timeout"] == 500


def test_check_influx_logs_when_ping_returns_false(monkeypatch, capsys):
    """
    ping() swallows its exception and sends the detail to a logger nothing in
    backend/ configures, so without a print here the most likely failure mode
    in production leaves no server-side record at all.
    """
    _patch_influx_client(monkeypatch, _FakeInfluxClient(ping=False))

    check_influx()

    assert "influxdb ping failed" in capsys.readouterr().out


def test_check_influx_closes_client_on_failure(monkeypatch):
    client = _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket_raises=Exception("nope"))
    )

    check_influx()

    assert client.closed is True


def test_check_influx_splits_the_budget_across_its_two_calls_in_milliseconds(monkeypatch):
    """
    The timeout parameter is milliseconds, not seconds, and the probe makes two
    server calls that must fit one budget together -- so a 2.0s budget gives
    each call 1000ms, not 2000ms. (Divided again by resolved address count;
    pinned to 1 here so this test is about the unit and the split only.)
    """
    recorder = {}
    monkeypatch.setattr(health_service, "_address_count", lambda url: 1)
    _patch_influx_client(
        monkeypatch, _FakeInfluxClient(ping=True, bucket=object()), recorder=recorder
    )

    check_influx(timeout_s=2.0)

    assert recorder["timeout"] == 1000


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def _patch_probes(monkeypatch, postgres, influx):
    calls = []

    def fake_postgres(*a, **kw):
        calls.append("postgresql")
        return postgres

    def fake_influx(*a, **kw):
        calls.append("influxdb")
        return influx

    monkeypatch.setattr(health_service, "check_postgres", fake_postgres)
    monkeypatch.setattr(health_service, "check_influx", fake_influx)
    return calls


_UP = {"status": "up", "latency_ms": 3}


def test_readiness_ready_when_both_up(monkeypatch):
    _patch_probes(monkeypatch, _UP, _UP)

    body, code = check_readiness()

    assert code == 200
    assert body["status"] == "ready"
    assert set(body["checks"]) == {"postgresql", "influxdb"}


def test_readiness_not_ready_when_postgres_down(monkeypatch):
    _patch_probes(monkeypatch, {"status": "down", "reason": REASON_UNREACHABLE}, _UP)

    body, code = check_readiness()

    assert code == 503
    assert body["status"] == "not_ready"


def test_readiness_not_ready_when_influx_down(monkeypatch):
    _patch_probes(monkeypatch, _UP, {"status": "down", "reason": REASON_UNHEALTHY})

    body, code = check_readiness()

    assert code == 503
    assert body["status"] == "not_ready"


def test_readiness_reports_both_reasons_when_both_down(monkeypatch):
    """No short-circuit: a caller must learn the state of both dependencies."""
    calls = _patch_probes(
        monkeypatch,
        {"status": "down", "reason": REASON_UNREACHABLE},
        {"status": "down", "reason": REASON_UNHEALTHY},
    )

    body, code = check_readiness()

    assert code == 503
    assert body["checks"]["postgresql"]["reason"] == REASON_UNREACHABLE
    assert body["checks"]["influxdb"]["reason"] == REASON_UNHEALTHY
    assert calls == ["postgresql", "influxdb"]


def test_reason_vocabulary_is_closed(monkeypatch):
    allowed = {REASON_UNREACHABLE, REASON_TIMEOUT, REASON_UNHEALTHY}
    for reason in list(allowed):
        _patch_probes(monkeypatch, {"status": "down", "reason": reason}, _UP)
        body, _ = check_readiness()
        assert body["checks"]["postgresql"]["reason"] in allowed


def test_readiness_never_propagates_an_escaped_probe(monkeypatch):
    """
    The no-leak guarantee rests on nothing escaping this module, because the
    route has no try/except by design. An escape becomes a 500, and under
    FLASK_DEBUG the debugger renders the DSN password into the body.
    """
    secret = "dsn=postgresql://postgres:hunter2@10.0.0.5/AMR_Prod"

    def escaping(*a, **k):
        raise RuntimeError(secret)

    monkeypatch.setattr(health_service, "check_postgres", escaping)
    monkeypatch.setattr(health_service, "check_influx", lambda *a, **k: _UP)

    body, code = check_readiness()

    assert code == 503
    assert secret not in repr(body)
    assert body["checks"]["postgresql"] == {
        "status": "down",
        "reason": REASON_UNREACHABLE,
    }


# --------------------------------------------------------------------------
# Escape paths found in review
# --------------------------------------------------------------------------

def test_dispose_failure_does_not_discard_the_sanitised_result(monkeypatch):
    """
    A raise in the finally block replaces the already-built return value. The
    probe had correctly classified the failure; dispose() threw it away.
    """
    class _DisposeRaises(_FakeEngine):
        def dispose(self):
            raise RuntimeError("dsn=postgresql://postgres:hunter2@db")

    _patch_create_engine(
        monkeypatch, engine=_DisposeRaises(on_connect=OSError("refused"))
    )

    result = check_postgres()

    assert result == {"status": "down", "reason": REASON_UNREACHABLE}


def test_non_ascii_exception_text_does_not_escape_the_probe(monkeypatch):
    """
    Exception text is locale-influenced. Interpolating it raw into a print
    raises UnicodeEncodeError on a cp1252 stdout -- the bug class already in
    docs/known_issues.md -- and that escapes past the sanitised return.
    """
    _patch_create_engine(monkeypatch, raises=OSError("szőlő refused"))

    result = check_postgres()

    assert result["status"] == "down"
    assert health_service._ascii("szőlő").isascii()


def test_check_postgres_unhealthy_when_server_refuses_credentials(monkeypatch):
    """
    A rotated password must not read as "unreachable": that sends the operator
    to check firewalls while the server is up and refusing the login.
    """
    class _Orig:
        pgcode = "28P01"   # invalid_password

    rejected = OSError("password authentication failed")
    rejected.orig = _Orig()
    _patch_create_engine(monkeypatch, raises=rejected)

    result = check_postgres()

    assert result == {"status": "down", "reason": REASON_UNHEALTHY}


def test_budget_is_floored_above_zero(monkeypatch):
    """0 and negatives read as "no limit" to libpq and urllib3, not "no wait"."""
    assert health_service._budget(0.0) > 0
    assert health_service._budget(-5) > 0
    assert health_service._budget("not-a-number") == 2.0
