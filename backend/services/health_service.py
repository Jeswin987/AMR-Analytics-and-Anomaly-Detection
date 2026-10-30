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
import time

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from backend.config import Config

STATUS_UP   = "up"
STATUS_DOWN = "down"

REASON_UNREACHABLE = "unreachable"
REASON_TIMEOUT     = "timeout"
REASON_UNHEALTHY   = "unhealthy"


def _budget(timeout_s):
    """Seconds allowed for one probe."""
    if timeout_s is None:
        return float(Config.HEALTH_TIMEOUT_SECONDS)
    return float(timeout_s)


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
            # int() alone would floor a sub-second budget to 0, which libpq
            # reads as "wait forever" -- the opposite of a timeout.
            connect_args={"connect_timeout": max(1, int(budget))},
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return _up(started)
    except Exception as e:
        print(f"[ERROR] readiness: postgresql probe failed: {e}")
        return _down(REASON_TIMEOUT if _exhausted(started, budget) else REASON_UNREACHABLE)
    finally:
        if engine is not None:
            engine.dispose()
