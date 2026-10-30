# backend/services/health_service.py
"""
Readiness probes for the application's data dependencies.

This module imports only Config and the database libraries -- deliberately not
analytics_service -- so the probes can be unit-tested without booting Flask or
reaching a live dependency.

Probes are live and stateless. They never read the connection state held by
DataLoaderService, because that verdict is taken once at import time and never
refreshed, so it reports history rather than the present.

Every failure is reported through a closed vocabulary of reasons. No exception
text, server message, hostname or credential is ever placed in a return value;
the real error goes to the server log instead.
"""
import socket
import time
from urllib.parse import urlparse

from influxdb_client import InfluxDBClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from backend.config import Config

STATUS_UP   = "up"
STATUS_DOWN = "down"

REASON_UNREACHABLE = "unreachable"
REASON_TIMEOUT     = "timeout"
REASON_UNHEALTHY   = "unhealthy"


_MIN_BUDGET_SECONDS = 0.001

# SQLSTATE classes that mean "the server answered, and said no". Without these
# a rotated password reports as "unreachable", sending the reader to look at
# firewalls while the database is up and refusing credentials.
_PG_UNHEALTHY_SQLSTATES = frozenset({
    "28P01",   # invalid_password
    "28000",   # invalid_authorization_specification
    "3D000",   # invalid_catalog_name  (database does not exist)
    "42501",   # insufficient_privilege
})


def _budget(timeout_s):
    """
    Seconds allowed for one probe, floored above zero.

    A zero or negative budget is a configuration error, and both libpq and
    urllib3 read it as "no limit" rather than "no waiting" -- the opposite of
    what the operator who typed it intended. Flooring here keeps a typo in
    .env from turning every readiness poll into an unbounded wait.
    """
    raw = Config.HEALTH_TIMEOUT_SECONDS if timeout_s is None else timeout_s
    try:
        value = float(raw)
    except (TypeError, ValueError):
        print(f"[WARN] readiness: unusable HEALTH_TIMEOUT_SECONDS, using 2.0")
        return 2.0
    return max(_MIN_BUDGET_SECONDS, value)


def _ascii(text_value):
    """
    Make server text safe to print.

    Exception text is attacker- and locale-influenced. A non-ASCII character
    in it raises UnicodeEncodeError on a cp1252 stdout, and that exception
    escapes the probe, bypasses the sanitised return value, and reaches the
    route -- which has no try/except by design. Flattening to ASCII keeps the
    diagnosis while making the print itself unable to fail.
    """
    return str(text_value).encode("ascii", "replace").decode("ascii")


def _address_count(url):
    """
    How many addresses a URL's host resolves to.

    urllib3 applies its timeout per connection attempt, and
    socket.create_connection walks every resolved address in turn, so a host
    like "localhost" (::1 and 127.0.0.1) takes n x timeout to fail. Dividing
    the per-attempt timeout by n is what keeps the probe inside its budget.
    """
    try:
        host = urlparse(url).hostname or "localhost"
        port = urlparse(url).port or 8086
        return max(1, len(socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)))
    except Exception:
        return 1


def _up(started_at):
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    return {"status": STATUS_UP, "latency_ms": elapsed_ms}


def _down(reason):
    return {"status": STATUS_DOWN, "reason": reason}


def _exhausted(started_at, budget):
    return (time.monotonic() - started_at) >= budget


def check_postgres(timeout_s=None):
    """
    Probe PostgreSQL with a bounded connect timeout. Never raises.

    Uses a throwaway NullPool engine rather than the application's shared one:
    this proves the database is reachable and answering, and keeps the probe
    from disturbing the pool the request path depends on.
    """
    budget  = _budget(timeout_s)
    started = time.monotonic()
    engine  = None
    try:
        engine = create_engine(
            Config.DATABASE_URL,
            poolclass=NullPool,
            connect_args={
                # int() alone would floor a sub-second budget to 0, which libpq
                # reads as "wait forever" -- the opposite of a timeout.
                "connect_timeout": max(1, int(budget)),
                # connect_timeout bounds the handshake only. Without this a
                # server that accepts the connection and then stalls (dropped
                # by a stateful firewall, pinned on a lock) makes SELECT 1
                # block forever and /api/ready never answer at all.
                "options": f"-c statement_timeout={max(1, int(budget * 1000))}",
            },
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return _up(started)
    except Exception as e:
        print(f"[ERROR] readiness: postgresql probe failed: {_ascii(e)}")
        if _exhausted(started, budget):
            return _down(REASON_TIMEOUT)
        if _pg_rejected_us(e):
            return _down(REASON_UNHEALTHY)
        return _down(REASON_UNREACHABLE)
    finally:
        if engine is not None:
            # dispose() must not be able to replace an already-built, sanitised
            # return value with a raised exception: the route has no try/except
            # and a raise there surfaces as a 500 carrying the DSN.
            try:
                engine.dispose()
            except Exception as e:
                print(f"[WARN] readiness: postgresql engine dispose failed: {_ascii(e)}")


def _pg_rejected_us(exc):
    """True when the server answered and refused, rather than being unreachable."""
    sqlstate = getattr(getattr(exc, "orig", None), "pgcode", None)
    return sqlstate in _PG_UNHEALTHY_SQLSTATES


def check_influx(timeout_s=None):
    """
    Probe InfluxDB for reachability and then for credentials. Never raises.

    Two calls, because one is not enough. The InfluxDB *server* does not
    enforce authorization on /ping -- it answers 204 for any reachable
    instance -- so ping() alone reports "up" for an expired or wrong token.
    (The client does send the Authorization header on /ping; the token simply
    is not checked there. auth_settings=[] in the generated service layer
    distinguishes nothing: buckets_service declares the same.) The bucket
    lookup that follows is checked by the server, so it is what actually
    validates INFLUX_TOKEN. Do not remove it.

    health() is deliberately never called: its HealthCheck.message carries raw
    server text, and the surest way not to leak that is never to fetch it.
    """
    budget    = _budget(timeout_s)
    started   = time.monotonic()
    client    = None
    reachable = False

    # An empty token cannot authenticate, and Config defaults it to "". Saying
    # so without a round trip is both faster and more honest than letting the
    # server reject us.
    if not Config.INFLUXDB["token"]:
        print("[WARN] readiness: influxdb token is not configured")
        return _down(REASON_UNHEALTHY)

    # Two calls share the budget, and each is divided again by the number of
    # addresses the host resolves to, because urllib3's timeout is per
    # connection attempt and create_connection walks every address.
    per_call_ms = max(1, int((budget / 2) * 1000 / _address_count(Config.INFLUXDB["url"])))
    try:
        client = InfluxDBClient(
            url=Config.INFLUXDB["url"],
            token=Config.INFLUXDB["token"],
            org=Config.INFLUXDB["org"],
            timeout=per_call_ms,   # influxdb-client takes milliseconds
            retries=False,         # already the library default; pinned explicitly
        )
        if not client.ping():
            # ping() swallows its own exception and returns a bare False, and
            # sends the detail to a logger nothing configures, so this is the
            # only place the failure can still be surfaced.
            if _exhausted(started, budget):
                print("[WARN] readiness: influxdb ping timed out")
                return _down(REASON_TIMEOUT)
            print("[WARN] readiness: influxdb ping failed, host unreachable")
            return _down(REASON_UNREACHABLE)
        reachable = True

        if _exhausted(started, budget):
            print("[WARN] readiness: influxdb budget spent before the token check")
            return _down(REASON_TIMEOUT)

        if client.buckets_api().find_bucket_by_name(Config.INFLUXDB["bucket"]) is None:
            print("[WARN] readiness: influxdb bucket missing or not visible to this token")
            return _down(REASON_UNHEALTHY)
        return _up(started)
    except Exception as e:
        print(f"[ERROR] readiness: influxdb probe failed: {_ascii(e)}")
        if _exhausted(started, budget):
            return _down(REASON_TIMEOUT)
        return _down(REASON_UNHEALTHY if reachable else REASON_UNREACHABLE)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as e:
                print(f"[WARN] readiness: influxdb client close failed: {_ascii(e)}")


def check_readiness():
    """
    Probe every dependency and return (response_body, http_status).

    Both probes always run; there is no short-circuit on the first failure, so
    one request always reports the state of every dependency. The status is 200
    only when all of them are up.
    """
    checks = {
        "postgresql": _guarded(check_postgres, "postgresql"),
        "influxdb":   _guarded(check_influx, "influxdb"),
    }
    ready = all(check["status"] == STATUS_UP for check in checks.values())
    body  = {"status": "ready" if ready else "not_ready", "checks": checks}
    return body, (200 if ready else 503)


def _guarded(probe, name):
    """
    Last line of defence: no probe may propagate.

    The probes are written not to raise, but the whole no-leak guarantee rests
    on that holding, because the route has no try/except on purpose. If one
    ever does raise, Flask turns a correct 503 into a 500 -- and under
    FLASK_DEBUG the debugger renders the traceback, DSN password included, into
    the response body. Converting an escape into a sanitised "down" keeps that
    structural, rather than a property of every future edit.
    """
    try:
        return probe()
    except BaseException as e:
        print(f"[ERROR] readiness: {name} probe escaped: {_ascii(e)}")
        return _down(REASON_UNREACHABLE)
