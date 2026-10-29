# Known issues — as of 2026-10-29

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
