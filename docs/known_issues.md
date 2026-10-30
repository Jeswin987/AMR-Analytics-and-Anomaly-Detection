# Known issues — as of 2026-10-30

Open items. Background: `docs/project_review_2026-08-11.md` and recent commit history.

- **`.env` binds to all interfaces.** `FLASK_HOST=0.0.0.0` — bind to loopback for dev.
- **Route handlers leak internals.** `get_devices`, `get_device_data`, `get_mission_data`, and
  `cache_clear` in `backend/api/routes.py` return `str(e)` to the client.
- **`POST /api/cache/clear` is unauthenticated.** The `cache_clear` handler has no CSRF
  protection or auth.
- **Unsynchronized device-name cache.** `AnalyticsService._get_device_name`
  (`backend/services/analytics_service.py`) reads and writes `_device_name_map` outside
  `_cache_lock`.
- **`requirements.txt` is not reproducible.** Uses `>=` rather than `==`.
- **Stale one-shot patch scripts.** `patch_dashboard_xss.py` and `patch_small_fixes.py` in the
  repo root are already applied; safe to delete.
- **`backend/` logs with `print()` instead of `logging`.** There is no `logging` usage anywhere
  under `backend/`, so every diagnostic goes to bare stdout with no levels, timestamps, or
  handler-level encoding control. This is what allowed a single emoji in
  `PostgreSQLLoader.__init__` to abort startup whenever stdout was redirected; pinning the
  encoding in `run.py` guards the symptom, moving to `logging` addresses the cause.
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

## Resolved

- ~~**`GET /api/health` reports `ok` while InfluxDB is down.**~~ Fixed 2026-10-30. `/api/health` is
  now liveness only (`{"status": "alive"}`, no I/O) and `GET /api/ready` live-probes both
  dependencies with a bounded timeout, reports each separately, and returns 503 when either is
  down. The InfluxDB probe follows `ping()` with an authenticated bucket lookup, because `/ping`
  is unauthenticated and would report `up` for a wrong or expired token. See
  `backend/services/health_service.py` and `docs/superpowers/specs/2026-10-30-health-readiness-design.md`.
