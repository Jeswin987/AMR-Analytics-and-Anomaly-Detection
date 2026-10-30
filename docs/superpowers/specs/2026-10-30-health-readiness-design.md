# Health and readiness probes — design

**Date:** 2026-10-30
**Status:** approved
**Known issue addressed:** "`GET /api/health` reports `ok` while InfluxDB is down" (`docs/known_issues.md`)

## Intent and why

`GET /api/health` returns a hardcoded `{"status": "ok"}` and probes nothing. It answered 200
during a run whose own startup log recorded `InfluxDB health: fail`, so the endpoint cannot be
trusted by a human or by a monitor. Today the reader is a developer at a terminal; later it is an
uptime monitor or a deployment platform.

A single endpoint cannot serve both jobs. A deployment platform reacts to a failing health check
by restarting or de-registering the process, which fixes nothing when the broken dependency is an
external InfluxDB and can produce a restart loop. So the two concerns are split.

## Goals

- `/api/health` answers "is this process alive" with no I/O.
- `/api/ready` answers "are my dependencies usable right now" by probing live, per request.
- Report each dependency separately, so the reader learns *which* one is broken.
- Validate the InfluxDB token, not merely reachability.
- Never put raw server text in a response body.

## Non-goals

- Fixing the false `[DATA] PostgreSQL connected` log line. Recorded in `docs/known_issues.md` instead.
- Adding `connect_timeout` to the application's shared SQLAlchemy engine.
- Refactoring the module-level `AnalyticsService()` singleton to lazy initialisation.
- Any authentication on either endpoint.

## Anti-goals

- A readiness check that reports `up` for an InfluxDB that is reachable but rejecting the token.
- Any response that echoes an exception, a `HealthCheck.message`, a hostname, or a token.
- A probe that reads boot-time connection state instead of probing live.

## Constraints

- No new runtime dependency. `pytest==8.3.4` is dev-only, in a new `requirements-dev.txt` pinned with `==`.
- ~2 second timeout per dependency, from `Config.HEALTH_TIMEOUT_SECONDS` (default `2.0`).
- `reason` is drawn from a closed vocabulary: `unreachable`, `timeout`, `unhealthy`.
- Real errors are logged server-side with the existing `[ERROR]` print convention, so failures are
  surfaced rather than swallowed, while the response stays sanitised.
- Print statements stay ASCII-only.

## Decided interface

    GET /api/health  -> 200  {"status": "alive"}

    GET /api/ready   -> 200  {"status": "ready",
                              "checks": {"postgresql": {"status": "up", "latency_ms": 12},
                                         "influxdb":   {"status": "up", "latency_ms": 34}}}

                     -> 503  {"status": "not_ready",
                              "checks": {"postgresql": {"status": "up", "latency_ms": 11},
                                         "influxdb":   {"status": "down", "reason": "unhealthy"}}}

## Decided approach

- New `backend/services/health_service.py`, importing only `Config` and the two database
  libraries. It deliberately does not import `analytics_service`, so its unit tests need no app
  boot and no live dependency.
- **PostgreSQL probe:** a short-lived `create_engine(Config.DATABASE_URL,
  connect_args={"connect_timeout": N}, poolclass=NullPool)`, `SELECT 1`, then dispose. This proves
  the database is reachable and answering. It does not exercise the application's shared pool;
  that is accepted.
- **InfluxDB probe:** a fresh `InfluxDBClient(..., timeout=<ms>)`, then `ping()` for reachability,
  then `buckets_api().find_bucket_by_name(Config.INFLUXDB['bucket'])` to force an authenticated
  call. `/ping` is declared `auth_settings=[]` in influxdb-client 1.50.0, so it sends no
  credentials and a wrong or expired token would otherwise report `up`. A fresh client is used
  because the boot-time client may be `None` and carries a 10 second timeout.
- `ping()` swallows every exception and returns a bare `False`, so `timeout` is distinguished from
  `unreachable` by elapsed time against the budget.
- Probes run sequentially. Worst case ~4s, acceptable at monitor frequency.
- Two independent guarantees keep server text out of responses: the closed `reason` vocabulary
  means no exception is ever formatted into the body, and preferring `ping()` over `health()`
  means `HealthCheck.message` never enters the process.

## Left to the builder

Internal helper names and shapes inside `health_service.py`, log message wording, and the exact
layout of test fixtures.
