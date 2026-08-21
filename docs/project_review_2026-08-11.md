# Project Review — 2026-08-11

## What This Project Is

This is an **operational analytics and anomaly detection platform for Autonomous Mobile Robots (AMRs)** manufactured by TrizLabz. The system has two distinct subsystems:

1. **A real-time fleet monitoring dashboard** (Flask web app) — shows per-device utilization, task performance, and battery analytics for a fleet of AMR robots, pulling live data from PostgreSQL (missions) and InfluxDB (telemetry time-series).

2. **An offline hybrid anomaly detection pipeline** (CLI scripts) — scores historical missions using a two-component model: an Isolation Forest trained on telemetry-derived features, combined with a rule-based detector scoring error-code patterns. Produces per-mission Normal/Warning/Critical labels.

These two subsystems are **architecturally independent** — the web dashboard does not use the anomaly detection model, and the anomaly scripts do not use the dashboard's transformer or data loader. They share only the `backend/ml/` directory (where the trained model lives) and the `ml_features_v3.csv` data file.

### Data Flow

```
                  ┌─────────────────────────────────────────────┐
                  │         SUBSYSTEM 1: WEB DASHBOARD          │
                  │                                             │
  PostgreSQL ─────┤─→ DataLoaderService ─→ TrizLabzTransformer  │
  (missions)      │     (data_loader.py)    (trizlabz_transformer.py)
                  │                              │              │
  InfluxDB ───────┤─→ DataLoaderService ─→ AnalyticsService ────┤─→ Flask API
  (telemetry)     │                    (analytics_service.py)   │   (routes.py)
                  │     battery resampling, drain attribution   │       │
                  │                                             │       ▼
                  └─────────────────────────────────────────────┘   dashboard.html
                                                                   dashboard.js
                                                                   dashboard.css

                  ┌─────────────────────────────────────────────┐
                  │     SUBSYSTEM 2: ANOMALY DETECTION (CLI)    │
                  │                                             │
  ml_features_v3  │                                             │
     .csv ────────┤─→ feature_extractor.py ─→ train_model.py   │
                  │   (load_and_prepare)       (StandardScaler  │
  baseline_208    │                             + IsolationForest│
  _mission_ids    │                             → backend/ml/)  │
     .csv ────────┤                                             │
                  │                          ─→ batch_score.py  │
                  │                             (anomaly_scorer  │
                  │                              + rule_based   │
                  │                              _detector)     │
                  │                                 │           │
                  │                          ─→ explain_anomaly │
                  │                              .py            │
                  │                                 │           │
                  │                          anomaly_scores     │
                  │                          _294.csv           │
                  └─────────────────────────────────────────────┘
```

### Inputs
- **PostgreSQL** (`AMR_Prod`): `mission_executions` table (mission name, device ID, status, waypoints, start/end times, errors)
- **InfluxDB** (`device_core` bucket): `device_telemetry` measurement (battery %, speed, position, status, driving mode, errors — tagged by `deviceId`)
- **`ml_features_v3.csv`**: pre-extracted feature matrix (294 missions × ~30+ columns including speed, distance, duration, error codes)
- **`baseline_208_mission_ids.csv`**: curated list of 208 "known-clean" missions for Isolation Forest training

### Outputs
- **Web dashboard**: real-time device-level KPIs (utilization rate, task success rate, battery health), hourly charts, per-mission drill-down with battery attribution
- **`anomaly_scores_294.csv`**: per-mission labels (Normal/Warning/Critical) with IF scores, error labels, and final hybrid labels
- **`backend/ml/model.pkl`, `scaler.pkl`, `model_metadata.json`**: persisted model artifacts

---

## Architecture

### Root-level scripts (offline CLI tooling)

| File | Responsibility |
|---|---|
| [`postgres.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/postgres.py) | CSV-to-PostgreSQL migration script. Reads CSV files from `Data/` directory and loads them into PostgreSQL tables using SQLAlchemy. **Not a general DB connector** — it's a one-time data loading tool. |
| [`feature_extractor.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/feature_extractor.py) | Shared data prep for the anomaly pipeline. `load_and_prepare()` reads `ml_features_v3.csv`, filters to one robot (XCR), computes `physical_obstruction_score` and `connectivity_failure_score` from raw error codes, cross-checks `has_escalation`, and applies feature caps. |
| [`anomaly_scorer.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/anomaly_scorer.py) | Hybrid scoring engine. `score_batch()` runs both Isolation Forest scoring (only on "Mar5" missions with valid telemetry) and rule-based error scoring, then combines via `worst_label()` into a `final_label`. |
| [`rule_based_detector.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/rule_based_detector.py) | Rule-based half of hybrid detector. Scores `physical_obstruction_score`, `connectivity_failure_score`, and `has_escalation` against fixed thresholds → per-mission `error_label`. |
| [`train_model.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/train_model.py) | Training pipeline: loads features → fits StandardScaler on 292 valid-telemetry missions → trains Isolation Forest on 208-mission clean baseline → self-check → saves `model.pkl`, `scaler.pkl`, `model_metadata.json` to `backend/ml/`. |
| [`batch_score.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/batch_score.py) | Loads saved model and scores all 294 XCR missions via `anomaly_scorer.score_batch()`. Writes `anomaly_scores_294.csv`. |
| [`explain_anomaly.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/explain_anomaly.py) | CLI tool: given a `mission_id`, shows why it was labeled. Ranks IF features by deviation from baseline (z-scores using scaler stats, with robust median/MAD for `duration_efficiency`), shows which rule thresholds were crossed. |
| [`correlation_analysis.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/correlation_analysis.py) | Pre-training analysis: Pearson correlation matrix, VIF, flagged pairs (|r| ≥ 0.7), heatmap. Validates feature independence before training. |
| [`dump.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/dump.py) | Quick diagnostic: prints two specific missions' IF features side-by-side for manual comparison. |
| [`run.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/run.py) | Entry point for Flask app. Reads host/port from `Config`. |

### `backend/` (Flask web application)

| File | Responsibility |
|---|---|
| [`app.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/app.py) | Flask factory. Registers the `api_bp` blueprint and defines `GET /` (serves `dashboard.html`). |
| [`config.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/config.py) | Single `Config` class. PostgreSQL config (via `DATABASE_URL` env var), InfluxDB config, Flask debug/host/port, data directory paths. Loads `.env` from project root. |
| [`api/routes.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/api/routes.py) | 5 API routes under `/api/`: `GET /devices`, `GET /data/<device_id>`, `GET /mission/<device_id>/<mission_index>`, `POST /cache/clear`, `GET /health`. |
| [`services/data_loader.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/data_loader.py) | `DataLoaderService` orchestrating `PostgreSQLLoader` (SQLAlchemy) + `InfluxDBLoader` (influxdb-client). Handles 24h auto-window, staleness detection, ±6h InfluxDB buffer. Returns `{missions, telemetry, start_time, end_time}`. |
| [`services/analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py) | 949-line service: battery noise reduction (1-min median resampling + rate-of-change filtering), `BatteryAttributor` (per-mission battery drain interpolation), `AnalyticsService` (orchestrates load → transform → build response). Module-level singleton `_service` with public API functions. |
| [`transformers/trizlabz_transformer.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/transformers/trizlabz_transformer.py) | 6 transformer classes: `UtilizationTransformer`, `TaskPerformanceTransformer`, `BatteryTransformer` (with `ChargeCycleDetector`), `HourlyUtilizationTransformer`, `HourlyEnergyTransformer`, `TaskTimelineTransformer`. Module-level singleton with public wrapper functions. |
| [`ml/model.pkl`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/ml/model.pkl) | Serialized Isolation Forest (trained 2026-07-29, sklearn 1.9.0, 208 training rows). |
| [`ml/scaler.pkl`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/ml/scaler.pkl) | Serialized StandardScaler (fit on 292 rows). |
| [`ml/model_metadata.json`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/ml/model_metadata.json) | Training metadata: 7 IF features, 4 feature caps, thresholds, score convention, excluded mission IDs. |

### Frontend

| File | Responsibility |
|---|---|
| [`templates/dashboard.html`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/templates/dashboard.html) | Single-page layout with device selector, mission selector, date range picker, 3 tabbed views (Utilization, Task Performance, Battery & Energy), Chart.js canvases, data tables. |
| [`static/css/dashboard.css`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/static/css/dashboard.css) | Dark-mode styling. |
| [`static/js/dashboard.js`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/static/js/dashboard.js) | ~45KB client-side logic: device loading, chart rendering, tab switching, mission detail view, date range filtering. |
| [`static/js/chart.umd.min.js`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/static/js/chart.umd.min.js) | Vendored Chart.js (~205KB). |

---

## Pros

### 1. Thoughtful hybrid anomaly detection architecture

The anomaly pipeline uses a two-component design that's well-suited for this domain: the Isolation Forest catches statistical outliers in telemetry features (speed, distance, duration efficiency), while the rule-based detector flags domain-specific error patterns (physical obstruction codes, connectivity failures, escalations). The `worst_label()` combiner ensures the final label is the most severe of either component. The `anomaly_scorer.py` correctly restricts IF scoring to missions matching the training population ("Mar5" pattern only), explicitly labeling others as "Not scored" rather than extrapolating.

**Files:** [`anomaly_scorer.py:score_batch()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/anomaly_scorer.py#L68-L81), [`rule_based_detector.py:worst_label()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/rule_based_detector.py#L31-L40).

### 2. Disciplined shared feature prep prevents training/serving skew

`feature_extractor.py:load_and_prepare()` is used by **both** `train_model.py` and `batch_score.py`, and the feature caps are passed as arguments read from `model_metadata.json`. This eliminates the classic bug where training and inference compute features differently. The function also performs a cross-check of `has_escalation` against its expected definition and warns if there are mismatches.

**File:** [`feature_extractor.py:load_and_prepare()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/feature_extractor.py#L23-L82).

### 3. Rigorous training pipeline with multiple safety checks

`train_model.py` includes: (a) strict row-count assertions (expects exactly 294 XCR missions, exactly 208 baseline missions, exactly 2 dropped zero-telemetry missions), (b) sanity check that baseline error-code features are constant/zero, (c) self-scoring the baseline to verify <5% Critical rate, (d) all hyperparameters and design choices are documented with rationale (which ones come from the handoff doc vs. which are the developer's call).

**File:** [`train_model.py:main()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/train_model.py#L80-L190).

### 4. Robust anomaly explanation with domain-appropriate z-scoring

`explain_anomaly.py` uses the scaler's own mean/std for most features (ensuring consistency with the model's perspective), but switches to median/MAD for `duration_efficiency` with a clear mathematical justification in the docstring (right-skewed distribution where mean/std distorts relative importance). This is unusually thoughtful for a project of this size.

**File:** [`explain_anomaly.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/explain_anomaly.py#L1-L35) (docstring), lines [78-96](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/explain_anomaly.py#L78-L96) (implementation).

### 5. Thorough battery noise reduction pipeline

The analytics service addresses a real data quality problem (sub-minute battery oscillations in InfluxDB) with a principled multi-stage pipeline: 1-minute median resampling → rate-of-change filtering → device filtering → window trimming. The same clean data feeds all three battery views (drain chart, level chart, KPIs), ensuring internal consistency. The `BatteryAttributor` does per-mission drain interpolation accounting for mission overlap with telemetry intervals.

**Files:** [`analytics_service.py:_resample_battery()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L103-L143), [`_clean_battery()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L146-L170), [`BatteryAttributor._compute()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L267-L355).

### 6. Pre-training correlation analysis

`correlation_analysis.py` is a genuine pre-training validation step: Pearson correlation + VIF with actionable thresholds (|r| ≥ 0.7 flagged, VIF ≥ 5 concern, VIF ≥ 10 serious). It operates on the 208-mission baseline, produces machine-readable outputs (`correlation_matrix.csv`, `flagged_pairs.csv`, `vif_report.csv`), and gives specific recommendations. This addresses multicollinearity — a real concern with Isolation Forest since correlated features get double-weighted.

**File:** [`correlation_analysis.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/correlation_analysis.py) (entire file).

### 7. Well-documented bug fixes in the analytics service

The analytics service includes a detailed changelog in its module docstring documenting three specific bugs and their fixes (Bug 1: empty battery KPIs due to window mismatch, Bug 2: false charge cycle counts from noisy data, Bug 3: cache key mismatch preventing per-device cache clears). Each fix is annotated inline where it appears.

**File:** [`analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L1-L30) (docstring), lines [817-824](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L817-L824), [934-949](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L934-L949).

### 8. Smart fallback behavior in data loading

`DataLoaderService.load_data_for_device()` handles edge cases well: if no missions exist in the last 24h, it auto-discovers the most recent 24h window with data. If InfluxDB telemetry is empty in the initial window, it retries with a ±6h buffer. Timestamps are consistently normalized to tz-naive after loading.

**File:** [`data_loader.py:load_data_for_device()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/data_loader.py#L310-L427).

---

## Cons / Risks

### 1. **CRITICAL: InfluxDB token committed in `.env` (not version-controlled, but on disk)**

`.env` contains a full InfluxDB API token:
```
INFLUX_TOKEN=Yy6UI1Jwtra1S0xbgNPdtPv79HMNWep3FnTOVhTj2Hi1o2EASmAfN6F45jSNdykMRYkWnKnl1F6IV6vECmf2pA==
```
While `.env` is in `.gitignore` (confirmed at line 4), the token is still on disk in plaintext and could be leaked via backups, screenshots, or accidental commits. The PostgreSQL password (`root`) is hardcoded as a fallback default in `config.py` line 33 and `postgres.py` line 19.

**Files:** [`.env`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/.env#L2), [`config.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/config.py#L33) (line 33), [`postgres.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/postgres.py#L19) (line 19).

### 2. **SQL injection in `postgres.py:migrate_csv_to_postgres()`**

The `table_name` variable (from the `CSV_FILES` dict keys) is interpolated directly into SQL strings:
```python
result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name};"))
```
While `table_name` currently comes from a hardcoded dict (so it's safe in practice), this pattern is fragile — any future modification that accepts table names from user input would be exploitable. Compare with `data_loader.py` which uses parameterized queries via `:param` syntax throughout.

**File:** [`postgres.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/postgres.py#L116) (lines 116, 133).

### 3. **Flux query injection in `analytics_service.py:_get_device_name()`**

The `device_id` is interpolated directly into a Flux query string:
```python
flux = f'''
from(bucket: "device_core")
  ...
  |> filter(fn: (r) => r["deviceId"] == "{device_id}")
  |> first()
'''
```
If `device_id` comes from an API request (it does — `routes.py` line 39 takes it from the URL path), an attacker could inject arbitrary Flux. The same pattern exists in `data_loader.py:load_telemetry()` line 186.

**Files:** [`analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L632-L638), [`data_loader.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/data_loader.py#L186).

### 4. **The web dashboard and anomaly pipeline are completely disconnected**

The web app (`analytics_service.py`, `trizlabz_transformer.py`) monitors operational metrics (utilization, task success, battery) but **never scores missions for anomalies**. The anomaly pipeline (`anomaly_scorer.py`, `train_model.py`, `batch_score.py`) produces `anomaly_scores_294.csv` but this file is never read by the web app. The `model.pkl` and `scaler.pkl` exist in `backend/ml/` but only the CLI scripts load them. The dashboard shows zero anomaly detection results.

The two subsystems use entirely different data schemas:
- Dashboard: `missionName`, `deviceId`, `startedAt`, `completedAt`, `distanceTravelled`, `completedWaypoints`, `totalWaypoints`
- Anomaly pipeline: `mission_id`, `robot_uuid`, `mission_name`, `avg_speed_ms`, `speed_stddev`, `pct_time_moving`, `distance_efficiency`, `sob01_count`, etc.

These appear to be different phases of the same project that haven't been integrated.

**Files:** All of `analytics_service.py` (no imports from `anomaly_scorer`, `batch_score`, or `rule_based_detector`); [`routes.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/api/routes.py) (no anomaly-related endpoints).

### 5. **Module-level singleton instantiation triggers DB connections at import time**

In `analytics_service.py`:
```python
_service = AnalyticsService()  # line 925
```
This runs at import time, which cascades to `DataLoaderService()` → `PostgreSQLLoader()` (creates SQLAlchemy engine, prints connection message) → `InfluxDBLoader()` (creates InfluxDB client, checks health). This means:
- Importing the module (e.g., for tests) creates real database connections.
- If either database is unreachable at startup, the import will print errors but not crash — the app will start with a partially-initialized loader.
- The same pattern exists in `trizlabz_transformer.py` line 343 (`_service = TrizlabzTransformerService()`), though that one is lightweight.

**Files:** [`analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L925), [`data_loader.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/data_loader.py#L50-L52).

### 6. **In-process caching with no TTL, eviction, or multi-worker coordination**

`AnalyticsService` uses three plain dicts for caching:
```python
self._cache: Dict[str, dict] = {}           # processed results
self._raw_cache: Dict[str, dict] = {}       # raw DB data
self._attributor_cache: Dict[str, BatteryAttributor] = {}
```
Issues: (a) no TTL — cached data never expires unless explicitly cleared via `/api/cache/clear`, (b) no size limit — with many devices and date ranges, memory grows unbounded, (c) each Gunicorn/uWSGI worker would have its own independent cache (no shared state), (d) `_last_key` is set via `setattr` rather than `__init__` (line 855).

**File:** [`analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L615-L620).

### 7. **Background thread spawned on `fetch_device_list()` without synchronization**

`AnalyticsService.fetch_device_list()` spawns a daemon thread that pre-warms the cache for all devices:
```python
import threading
def warm():
    for dev in devices:
        self.fetch_dashboard_data(dev, force_refresh=True)
threading.Thread(target=warm, daemon=True).start()
```
This thread mutates `self._cache`, `self._raw_cache`, and `self._attributor_cache` concurrently with request-handling threads — none of these dicts are thread-safe. This could cause `RuntimeError: dictionary changed size during iteration` or silently corrupt cached data.

**File:** [`analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L758-L773).

### 8. **`postgres.py` has module-level side effects (prints at import)**

Lines 39-44 execute `print()` and `os.path.exists()` at import time:
```python
print(f"📁 Looking for CSV files in: {DATA_DIR}")
...
for table, path in CSV_FILES.items():
    exists = "✅" if os.path.exists(path) else "❌"
    print(f"   {exists} {table}: {os.path.basename(path)}")
```
This means importing `postgres.py` from any other module would produce console output. Currently nothing imports it, but the `if __name__ == "__main__"` guard only protects the migration function, not the print statements.

**File:** [`postgres.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/postgres.py#L39-L44).

### 9. **`contamination=0.15` is somewhat arbitrary for a baseline-only training set**

The Isolation Forest is trained on 208 "known-clean" baseline missions and told that 15% of them are anomalous (`contamination=0.15`). But the baseline is **curated to be clean** — so telling the model that 15% of clean data is anomalous creates a structural tension. The model will force-flag ~31 of 208 clean missions as anomalies. The self-check reports the distribution but doesn't validate whether the flagged baseline missions are actually outliers or just normal variance in clean data.

**Files:** [`train_model.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/train_model.py#L73) (line 73), [`model_metadata.json`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/ml/model_metadata.json#L20) (line 20).

### 10. **`SummaryBuilder.build()` accesses `schedulers.empty` but schedulers is always empty**

In `fetch_dashboard_data()`:
```python
schedulers = pd.DataFrame()  # line 787 — always empty
...
summary = self.summary_builder.build(utilization, task_perf, battery, schedulers)
```
`SummaryBuilder.build()` then checks `schedulers.empty` and tries to access `schedulers['totalMissionsCompleted']`, which would never succeed. The scheduler integration appears abandoned mid-implementation.

**Files:** [`analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L787) (line 787), [`analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L384-L405) (SummaryBuilder lines 384-405).

### 11. **`battery_health` is misleadingly named**

`BatteryTransformer.transform()` sets `battery_health = min(100.0, avg_battery)` — this is just the average state-of-charge (SOC) over the telemetry window, **not** a measure of battery degradation/health. The docstring acknowledges this ("This is NOT true degradation health") but the KPI name `battery_health` is still surfaced to the dashboard where it could mislead operators into thinking their batteries are healthy when they may actually be degrading.

**File:** [`trizlabz_transformer.py:BatteryTransformer.transform()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/transformers/trizlabz_transformer.py#L164-L213) (line 197).

### 12. **`app.py` defines a duplicate `GET /` route**

`app.py` defines `@app.route('/')` inline (line 21), but `routes.py` does not define a `/` route (it uses `url_prefix='/api'`). This is fine, but the `create_app()` function imports `api_bp` at the module level (line 6: `from backend.api.routes import api_bp`), which triggers the `analytics_service.py` module-level singleton at import time (see Con #5). There's no way to create the Flask app without connecting to both databases.

**File:** [`app.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/app.py#L5-L6).

### 13. **`HourlyUtilizationTransformer` has O(n×m) complexity**

For each hour in the time range, the transformer iterates over all working missions to check overlap:
```python
for hour_start in hours:
    for _, row in working.iterrows():
        overlap = ...
```
With h hours and m missions, this is O(h×m). For the default 24h window this is fine, but custom date ranges could span weeks or months, and `iterrows()` is pandas' slowest iteration method.

**File:** [`trizlabz_transformer.py:HourlyUtilizationTransformer.transform()`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/transformers/trizlabz_transformer.py#L243-L260).

### 14. **Vendored Chart.js with no version tracking**

`chart.umd.min.js` (205 KB) is vendored directly with no indication of which version it is. No `package.json`, no version comment, and no way to update it systematically.

**File:** [`chart.umd.min.js`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/static/js/chart.umd.min.js).

### 15. **`model_metadata.json` schema mismatch between what's saved and what the web app would expect**

The anomaly pipeline saves metadata with keys like `if_features`, `if_thresholds`, `feature_caps`, `scaler_fit_rows`, `scaler_excluded_mission_ids`. If the web dashboard ever tried to load this model (which it currently doesn't — see Con #4), it would need to understand this schema. But the dashboard's transformer (`trizlabz_transformer.py`) computes completely different features (utilization, task success, battery) with different column names. The model simply cannot be applied to dashboard data without a major integration effort.

**Files:** [`model_metadata.json`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/ml/model_metadata.json), [`trizlabz_transformer.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/transformers/trizlabz_transformer.py).

### 16. **`_swap_names` is defined as a nested function twice in the same class**

`AnalyticsService.fetch_dashboard_data()` defines a local `_swap_names` function at line 831, and `fetch_mission_data()` defines another identical one at line 904. These should be a shared method.

**File:** [`analytics_service.py`](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L831-L835) and [lines 904-908](file:///c:/Users/VICTUS/AMR-Analytics_app_V2/backend/services/analytics_service.py#L904-L908).

---

## Open Questions

1. **How is `ml_features_v3.csv` generated?** No script in this repo produces it. The `feature_extractor.py` reads it as input but doesn't create it. There must be an upstream data extraction step (possibly `extract_ml_features_v3.py`, referenced in `correlation_analysis.py` line 84) that isn't included in this repo.

2. **Is the anomaly pipeline intended to be integrated into the dashboard?** The `model.pkl` and `scaler.pkl` live in `backend/ml/` which suggests intent, but the dashboard never loads or uses them. Is this a planned Phase 2B?

3. **What is the `Data/` directory?** `config.py` references `Data/mission_executions.csv`, `Data/device_telemetry.csv`, `Data/scheduler_executions.csv`, and `Data/pois.json`. `postgres.py` reads from the same directory. But `Data/*.csv` is in `.gitignore`, and there's no indication whether this directory currently exists or is populated. Are these CSV backups of the database, or the primary data source?

4. **Why is `sklearn` listed in `requirements.txt` for the anomaly pipeline but not installed as a web dependency?** The web app's `requirements.txt` includes `flask`, `pandas`, `numpy`, `sqlalchemy`, `psycopg2-binary`, `influxdb-client`, `pytz`, `python-dotenv` — but not `scikit-learn` or `joblib`. The anomaly scripts would need a separate install. This supports the "two separate subsystems" interpretation but isn't explicitly documented.

5. **What generates the `baseline_208_mission_ids.csv`?** `correlation_analysis.py` (line 84) references `pre_training_validation.py` which isn't in the repo. The `pre_training_validation.txt` file exists (8.7 KB) but it's a text report, not the ID list.

6. **How is the system deployed?** No `Procfile`, Dockerfile, docker-compose, or deployment configuration exists. `run.py` uses Flask's dev server. Is there a separate deployment repo?

7. **Is the `postgres.py` migration script still needed?** It uses `if_exists='replace'` which would **destroy** existing PostgreSQL data. If the database is already populated (which it must be for the web app to work), running this script again would wipe it. There's a confirmation prompt, but no warning in the codebase about this.

8. **Why does `config.py` reference `pois.json` (Points of Interest)?** This suggests a map/spatial feature that isn't visible anywhere else in the codebase. Is this a planned feature, or a remnant?
