# Known issues — as of 2026-10-31

Open items. Background: `docs/project_review_2026-08-11.md` and recent commit history.

- **Route handlers leak internals.** `get_devices`, `get_device_data`, `get_mission_data`, and
  `cache_clear` in `backend/api/routes.py` return `str(e)` to the client.
- **`POST /api/cache/clear` is unauthenticated.** The `cache_clear` handler has no CSRF
  protection or auth.
- **Cache access is only partly synchronized.** In `backend/services/analytics_service.py`:
  - `clear_cache()` mutates `_cache`, `_raw_cache` and `_attributor_cache` without taking
    `_cache_lock`, racing the locked readers and writers in `fetch_dashboard_data`.
  - `_attributor_cache` is never guarded: it is written in `fetch_dashboard_data` and read and
    written in `fetch_mission_data` outside the lock.
  - `AnalyticsService._get_device_name` reads and writes `_device_name_map` outside
    `_cache_lock`.
  - Per-device clearing matches `k.startswith(device_id)`, ignoring the `|` key separator, so
    clearing device `ab` also clears every device whose ID starts with `ab`. It also leaves
    `_last_key[device_id]` pointing at the evicted key.
- **Device-name lookup ignores `INFLUX_BUCKET`.** `_get_device_name`
  (`backend/services/analytics_service.py`) hardcodes `from(bucket: "device_core")` in its Flux
  query instead of using `Config.INFLUXDB['bucket']`, so a non-default bucket silently yields
  fallback names.
- **A rejected device ID returns 200, not 400.** When `InfluxDBLoader.load_telemetry`
  (`backend/services/data_loader.py`) rejects an ID against `_DEVICE_ID_PATTERN`, it logs and
  returns an empty DataFrame. `GET /api/data/<id>` then answers HTTP 200 with empty telemetry
  data, so the client cannot tell a malformed ID from a device with no telemetry.
- **The app starts without `INFLUX_TOKEN`.** `Config.INFLUXDB['token']` (`backend/config.py`)
  defaults to `''` rather than raising like `DATABASE_URL` and `DB_PASSWORD`. Only `/api/ready`
  notices, by reporting InfluxDB down.
- **Unused import.** `transform_hourly_energy` is imported in
  `backend/services/analytics_service.py` and never used.
- **`requirements.txt` is not reproducible.** Uses `>=` rather than `==`.
- **`backend/` logs with `print()` instead of `logging`.** There is no `logging` usage anywhere
  under `backend/`, so every diagnostic goes to bare stdout with no levels, timestamps, or
  handler-level encoding control. This is what allowed a single emoji in
  `PostgreSQLLoader.__init__` to abort startup whenever stdout was redirected. Pinning the
  encoding in `run.py` would guard the symptom — it is **not** currently done there — and moving
  to `logging` addresses the cause.
- **`backend/` logs the PostgreSQL connection before it has one.** `PostgreSQLLoader.__init__`
  prints a connected message immediately after `create_engine()`, which is lazy and opens no
  socket, so the line proves nothing about reachability and reads as a successful connection even
  when the database is unreachable. It is also why a startup log can look healthy while the
  database is dead.
- **The service singleton is built at import time.** Importing `backend/api/routes.py` pulls in
  `analytics_service`, which constructs a module-level `AnalyticsService()`, which constructs
  `DataLoaderService` and therefore `InfluxDBLoader`. That constructor performs a real InfluxDB
  health call on import, so merely importing the app does network I/O with the client's 10 second
  default timeout, and the result is cached for the process lifetime and never refreshed. Tests
  have to point `INFLUX_URL` at a closed port to keep collection fast (see `tests/conftest.py`),
  and anything reading that boot-time state learns what was true at startup rather than now.
- **Readiness follow-ups.** Smaller items from the 2026-10-30 review of
  `backend/services/health_service.py`, none of them blocking:
  - Sub-second budgets are not honoured for PostgreSQL. `connect_timeout` is floored at 1 second
    (the alternative, `0`, means "wait forever" to libpq), so a `HEALTH_TIMEOUT_SECONDS` below
    `1.0` is silently raised. CLAUDE.md advertises the key as freely configurable without saying so.
  - `Config.HEALTH_TIMEOUT_SECONDS` crashes boot on non-numeric input with a bare `ValueError`,
    unlike the neighbouring `RuntimeError`s that name the offending variable. The probes handle a
    bad value; `Config` does not.
  - `/api/ready` sends no `Cache-Control: no-store`, so a proxy or monitor-side cache can serve a
    stale `200 ready`.
  - No test asserts that the PostgreSQL probe actually runs `SELECT 1` rather than merely opening a
    connection — `_FakeConnection.executed` is recorded and never checked — though "reachable *and
    answering*" is the probe's stated advantage over the shared pool.
  - `test_health_performs_no_dependency_io` guards only the `health_service.*` call path. Making
    liveness expensive via `create_engine`, `requests`, or the `analytics_service` singleton walks
    past it.
  - The suite does real network I/O at collection, via the import-time singleton above.
  - `print()` to a redirected stdout is block-buffered, so readiness `[WARN]` lines are lost
    entirely if the process is killed before the buffer flushes. Werkzeug's own request lines
    survive because they go to stderr. Verified the lines are emitted under `python -u`. This is
    the `print()`-instead-of-`logging` item biting a second time.
  - PEP 8: one blank line, not two, before the `/api/ready` decorator in `backend/api/routes.py`.

## Pre-production requirements

Gates, not backlog. These must be closed before this app is deployed anywhere reachable.

- **`/api/ready` must be protected or cached before deployment.** Each request opens a fresh
  PostgreSQL connection (`NullPool`, deliberately) plus two InfluxDB HTTP calls, with no auth, no
  throttle and no result cache. A burst of concurrent requests can exhaust `max_connections` on the
  production database, at which point the readiness probe becomes the cause of the outage it
  reports. Either put the endpoint behind auth or a network boundary, or give it a short (1–2s)
  result cache so a flood collapses to one probe. A cache is a deliberate departure from the
  design's "probe live, per request" decision
  (`docs/superpowers/specs/2026-10-30-health-readiness-design.md`) and should be recorded as such.

## Resolved

- ~~**`GET /api/health` reports `ok` while InfluxDB is down.**~~ Fixed 2026-10-30. `/api/health` is
  now liveness only (`{"status": "alive"}`, no I/O) and `GET /api/ready` live-probes both
  dependencies with a bounded timeout, reports each separately, and returns 503 when either is
  down. The InfluxDB probe follows `ping()` with a bucket lookup, because the InfluxDB server does
  not check the token on `/ping` and would report `up` for a wrong or expired one. See
  `backend/services/health_service.py` and `docs/superpowers/specs/2026-10-30-health-readiness-design.md`.
- ~~**`.env` binds to all interfaces.**~~ Resolved 2026-10-30 by the maintainer: `FLASK_HOST` is
  now `127.0.0.1`. Recorded on their report; `.env` is gitignored and was not read to confirm.
