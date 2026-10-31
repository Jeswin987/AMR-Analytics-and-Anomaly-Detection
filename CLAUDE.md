# AMR Analytics App V2

Flask backend + vanilla-JS dashboard for Autonomous Mobile Robot fleet analytics.
Data sources: PostgreSQL (missions/schedulers) + InfluxDB (telemetry time-series).
Anomaly detection: scikit-learn IsolationForest, trained offline.

## Entry points

- `run.py` → `backend.app:create_app()` → serves on `FLASK_HOST:FLASK_PORT` (`.env`).
- Dev: `python run.py`. Production is NOT yet set up (no gunicorn wrapper).
- Liveness: `GET /api/health` (no I/O). Readiness: `GET /api/ready` (live-probes PostgreSQL + InfluxDB, 503 if either is down).

## Layout

| Path | Purpose |
|---|---|
| `backend/app.py` | Flask factory, blueprint registration |
| `backend/api/routes.py` | HTTP routes (`/api/devices`, `/api/data/<id>`, `/api/mission/...`, `/api/cache/clear`, `/api/health`, `/api/ready`) |
| `backend/services/health_service.py` | Readiness probes; imports only `Config`, never `analytics_service` |
| `backend/services/analytics_service.py` | Core business logic — battery, utilization, caching, device-name map |
| `backend/services/data_loader.py` | SQL + Flux queries, parameterized |
| `backend/transformers/trizlabz_transformer.py` | `ChargeCycleDetector`, utilization transforms |
| `backend/config.py` | `.env` loader; raises on missing `DATABASE_URL` / `DB_PASSWORD` |
| `backend/ml/` | Pickled `model.pkl`, `scaler.pkl`, `model_metadata.json` (gitignored) |
| `backend/templates/dashboard.html` + `backend/static/js/dashboard.js` | Single-page dashboard |
| `anomaly_pipeline/` | Offline training + validation scripts (not called by the web app) |
| `analysis/` | Analysis artifacts (CSV/PDF outputs) |

## Config

All runtime secrets come from `.env` at repo root (gitignored). Required keys:
`DATABASE_URL`, `DB_PASSWORD`, `INFLUX_TOKEN`, `INFLUX_ORG`, `INFLUX_BUCKET`.
Optional: `FLASK_HOST` (default `127.0.0.1`), `FLASK_PORT` (`5001`), `FLASK_DEBUG` (`false`), `HOURS_WINDOW` (`24`), `HEALTH_TIMEOUT_SECONDS` (`2.0`).

## ML pipeline

- Train: `python anomaly_pipeline/train_model.py` (not `-m`: the scripts use bare sibling imports) — fits `StandardScaler` on 292 missions, `IsolationForest(contamination=0.15, n_estimators=…, random_state=…)` on the 208 zero-error baseline subset. Writes `model.pkl` + `scaler.pkl` + `model_metadata.json`.
- Score: `anomaly_pipeline/batch_score.py`, `held_out_validation.py`.

## Conventions

- Python 3.12+ (`venv/` present but gitignored).
- Tests: `pip install -r requirements-dev.txt`, then `python -m pytest` (config in `pytest.ini`). Coverage is health/readiness only — the rest of the app is still verified manually. Do not assume CI.
- `tests/conftest.py` sets `DATABASE_URL`/`DB_PASSWORD`/`INFLUX_URL` before importing `backend`, because `config.py` raises at import and `analytics_service` does network I/O at import. Keep that ordering.
- All DB queries use parameterized bindings (`data_loader.py:57-80` is the template to follow).
- Frontend data interpolated into DOM via `escapeHtml()` helper (`static/js/dashboard.js:37`). Follow that pattern for any new `innerHTML` assignments touching server data.
- Caching: `analytics_service.py` uses an in-process dict guarded by `_cache_lock`. Keys shaped `f"{device_id}|{start}|{end}"`.

## Environment
- Windows 11; Claude Code shell is Git Bash. Use forward-slash paths.
- Activate venv: `source venv/Scripts/activate`

## How to verify a change
1. Syntax check: `python -m compileall -q backend anomaly_pipeline`
2. Start app: `python run.py`, then `curl -s http://127.0.0.1:5001/api/health` must return 200.
3. For route/service changes: curl the affected endpoint and show me the response.
4. For dashboard changes: you can't see the browser, so tell me exactly what to check.
5. Never claim a change works without running steps 1–2.

## Working style
- Changes touching 3+ files: propose a plan first and wait for approval.
- Never delete files, commit, or push without asking.
- Keep diffs minimal; don't reformat unrelated code.

## Known issues

See `docs/known_issues.md` (last reviewed 2026-10-31).

## Don't

- Don't commit `.env`, `*.pkl`, `*.bak`, or files under `analysis/` with secrets.
- Don't add fallbacks that silently swallow DB/Influx errors — surface them.
- Don't add new `innerHTML` interpolations without `escapeHtml()`.
- Don't change `N_ESTIMATORS`, `RANDOM_STATE`, `IF_FEATURES`, `FEATURE_CAPS` (`train_model.py`) or `baseline_208_mission_ids.csv` without retraining AND re-scoring all missions; `IF_THRESHOLDS` changes need re-scoring. `CONTAMINATION` has no effect on scores or labels (we use `score_samples()`; see phase_2A validation Test 2).
