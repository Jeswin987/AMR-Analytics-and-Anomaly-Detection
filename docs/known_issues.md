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
- **`GET /api/health` reports `ok` while InfluxDB is down.** The `health` handler in
  `backend/api/routes.py` returns a static `{"status": "ok"}` and probes no dependency. It
  returned 200 during a run whose own startup log recorded `InfluxDB health: fail`, so any
  uptime check pointed at it will miss a telemetry outage.
