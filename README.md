# AMR Fleet Analytics & Anomaly Detection

> **Fleet analytics dashboard + offline hybrid anomaly detection pipeline for Autonomous Mobile Robot (AMR) operations.**
> Real-time telemetry visualisation, mission drill-down, battery attribution, utilization tracking, and Isolation Forest + rule-based anomaly scoring.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Dashboard](#running-the-dashboard)
- [Running the Anomaly Detection Pipeline](#running-the-anomaly-detection-pipeline)
- [API Reference](#api-reference)
- [Data Sources](#data-sources)
- [Project Structure](#project-structure)
- [Known Limitations](#known-limitations)

---

## Overview

This project has two independent subsystems that share only the `backend/ml/` model artifacts folder:

| Subsystem | What it does | Status |
|---|---|---|
| **Dashboard** (`backend/`) | Live Flask app reading PostgreSQL + InfluxDB directly — utilization, task performance, battery KPIs, mission drill-down | Complete |
| **Anomaly detection pipeline** (`anomaly_pipeline/`) | Offline hybrid model (Isolation Forest + rule-based error detection) trained on a clean mission baseline, scored via CLI scripts | Complete (Phase 2A) |

A third phase — a per-robot efficiency scoring system — is documented but paused; see [`docs/phase_2B_efficiency_score_status_2026-08-18.md`](docs/phase_2B_efficiency_score_status_2026-08-18.md) for the full status and reasoning.

The dashboard connects to two live databases:

| Source | Technology | Data |
|---|---|---|
| Relational | **PostgreSQL** (`AMR_Prod`) | Mission executions, scheduler runs, waypoints |
| Time-series | **InfluxDB** | Battery %, speed, position, device status |

The dashboard provides:
- **Device overview** — utilization rate, working vs idle hours, task success/failure, battery SOC curve, hourly drain chart
- **Mission drill-down** — per-mission battery attribution (direct or interpolated), duration, distance, drain rate
- **Battery attribution** — 1-minute resampled, noise-filtered telemetry with time-proportional drain sharing across concurrent missions

The anomaly pipeline provides:
- **Isolation Forest** scoring on 7 behavioral features (speed, distance, duration efficiency, etc.), trained on a 208-mission clean baseline
- **Rule-based detection** on 3 error-code-derived features (zero variance in the clean baseline, so unsuitable for the IF model)
- **Hybrid final label** — the worse of the two components wins
- **Per-mission explanations** via robust (median/MAD) and standard z-scores, showing exactly which features drove a mission's score

---

## Architecture

```
Browser (dashboard.html)
  └── CSS: /static/css/dashboard.css
  └── JS : /static/js/dashboard.js   (Chart.js charts, API calls)
  └── Chart.js bundled offline: /static/js/chart.umd.min.js

Flask (run.py → backend/app.py)
  └── GET  /                          → serves dashboard.html
  └── GET  /api/devices               → device list
  └── GET  /api/data/<device_id>      → full dashboard payload (with cache)
  └── GET  /api/mission/<id>/<index>  → per-mission drill-down data
  └── POST /api/cache/clear           → cache eviction
  └── GET  /api/health                → liveness probe

analytics_service.py  ←  data_loader.py  ←  PostgreSQL + InfluxDB
trizlabz_transformer.py  (6 transform functions)
BatteryAttributor  (1-min resampled, noise-cleaned attribution)
```

```
Anomaly detection pipeline (anomaly_pipeline/) — run independently via CLI

ml_features_v3.csv
        │
        ▼
feature_extractor.py  (shared load/filter/cap logic)
        │
        ├──► train_model.py  ──► backend/ml/{model.pkl, scaler.pkl, model_metadata.json}
        │
        └──► batch_score.py  ──► anomaly_scorer.py + rule_based_detector.py
                                        │
                                        ▼
                              anomaly_scores_294.csv (final_label per mission)

explain_anomaly.py <mission_id>  — on-demand, human-readable score breakdown
                                    for any mission already in anomaly_scores_294.csv
```

---

## Prerequisites

| Dependency | Version | Purpose |
|---|---|---|
| Python | ≥ 3.9 | Runtime |
| PostgreSQL | ≥ 13 | Mission / scheduler data |
| InfluxDB | ≥ 2.0 | Telemetry time-series |

---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd AMR-Analytics-and-Anomaly-Detection

# 2. Create a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 3. Install all dependencies
pip install -r requirements.txt
```

---

## Configuration

All secrets and environment settings are loaded from a `.env` file in the project root.
**Never commit `.env` to git** — it is excluded via `.gitignore`.

Create your own `.env` from the template below (do **not** copy your real `.env` — create `.env.example` by hand with placeholder values only, since anything copied from a real `.env` will contain live credentials):

```env
# PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/AMR_Prod
DB_USER=
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=5432
DB_NAME=AMR_Prod

# InfluxDB
INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=
INFLUX_ORG=
INFLUX_BUCKET=device_core

# Flask
FLASK_DEBUG=false        # set to true for local development only
FLASK_HOST=0.0.0.0
FLASK_PORT=5001
```

> **Security note:** `FLASK_DEBUG=true` enables the Flask interactive debugger, which can expose secret values. Always set `FLASK_DEBUG=false` outside local development. There is currently no authentication layer on any API route — see [Known Limitations](#known-limitations).

`DATABASE_URL` and `DB_PASSWORD` have no hardcoded fallback — the app will fail loudly on startup if either is missing from `.env`, rather than silently connecting with a default credential.

---

## Running the Dashboard

```bash
venv\Scripts\activate
python run.py
```

Open your browser at: **http://localhost:5001**

On startup the server will:
1. Connect to PostgreSQL and InfluxDB
2. Auto-select the first available device and load its data
3. Background-warm the cache for all discovered devices (safe under concurrent access — cache reads/writes are guarded by a lock, and duplicate in-flight loads for the same device are coalesced rather than re-run)

---

## Running the Anomaly Detection Pipeline

All pipeline scripts live in `anomaly_pipeline/` and are run independently of the dashboard — no live database connection required, just `ml_features_v3.csv` and (for training) `baseline_208_mission_ids.csv` in the locations shown in [Project Structure](#project-structure).

```bash
cd anomaly_pipeline

# 1. Train the Isolation Forest on the 208-mission clean baseline
#    Writes model.pkl, scaler.pkl, model_metadata.json to backend/ml/
python train_model.py

# 2. Score all missions with the trained hybrid model
#    Writes anomaly_scores_294.csv into anomaly_pipeline/
python batch_score.py

# 3. Get a human-readable explanation for any specific mission
python explain_anomaly.py <mission_id>
```

`explain_anomaly.py` requires `batch_score.py` to have already run, since it reads `anomaly_scores_294.csv` to know each mission's labels before explaining them.

`model.pkl` and `scaler.pkl` are not committed to this repo (see `.gitignore`) — run `train_model.py` once after cloning to regenerate them locally.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness check — returns `{"status": "ok"}` |
| `GET` | `/api/devices` | List all known devices (`id` + human-readable `name`) |
| `GET` | `/api/data/<device_id>` | Full dashboard payload for a device |
| `GET` | `/api/data/<device_id>?refresh=true` | Force cache bypass |
| `GET` | `/api/mission/<device_id>/<index>` | Per-mission drill-down (index = 0-based, most-recent-first) |
| `POST` | `/api/cache/clear` | Clear all cached data |
| `POST` | `/api/cache/clear?device=<id>` | Clear cache for a specific device |

> `/api/cache/clear` is unauthenticated and state-mutating — acceptable for local development on a private network, but should not be exposed without adding auth first. See [Known Limitations](#known-limitations).

### Dashboard Payload (`/api/data/<device_id>`)

```json
{
  "device_id": "...",
  "summary": {
    "working_hours": 6.2,
    "idle_hours": 17.8,
    "utilization_rate": 25.8,
    "tasks_completed": 12,
    "tasks_failed": 1,
    "success_rate": 92.3,
    "current_battery": 78.5,
    "battery_health": 81.2,
    "estimated_runtime": 6.3,
    "charge_cycles_today": 2
  },
  "hourly_utilization": [{"timestamp": "06-Apr 08:00", "utilization": 45.2}, ...],
  "hourly_drain":       [{"timestamp": "06-Apr 08:00", "drain_pct": 1.8}, ...],
  "hourly_level":       [{"timestamp": "06-Apr 08:00", "avg_level": 82.1, "min_level": 79.0, "max_level": 85.0}, ...],
  "task_timeline":      [{"timestamp": "06-Apr 08:00", "completed": 2, "failed": 0}, ...],
  "mission_rows":       [...],
  "telemetry_rows":     [...],
  "battery_rows":       [...]
}
```

---

## Data Sources

### PostgreSQL — `mission_executions` table

Key columns used:

| Column | Type | Notes |
|---|---|---|
| `"deviceId"` | `text` | Quoted — case-sensitive |
| `"startedAt"` | `timestamptz` | Mission start |
| `"completedAt"` | `timestamptz` | Mission end |
| `status` | `text` | `completed`, `cancelled`, `pending` — this dataset has no `failed` status |
| `"missionName"` | `text` | Human-readable name / pattern type (e.g. `Mar5`, `DemoMission`) |
| `"distanceTravelled"` | `numeric` | Metres |
| `"completedWaypoints"` | `int` | Waypoints finished |
| `"totalWaypoints"` | `int` | Total waypoints planned |
| `error`, `metadata` | `text` / `json` | Present in schema but not populated for cancelled missions in this dataset — no fault-attribution data available for why a mission was cancelled |

### InfluxDB — `device_core` bucket, measurement `device_telemetry`

> **Schema note:** `deviceId` and `endpointId` are InfluxDB **tags**, not fields. Flux tag-filters (`r["deviceId"] == "..."`) work correctly and are applied at query time for efficiency. A pandas-side fallback filter still runs after pivoting to handle UUID-prefix edge cases.

Key fields:

| Field | Type | Notes |
|---|---|---|
| `battery` | float | State of charge (%) |
| `speed` | float | Current speed (m/s) |
| `status` | string | Robot status |
| `driving` | bool/float | Whether robot is moving |
| `zoneId` | string | Current zone |
| `lastNodeId` | string | Last visited node |
| `operatingMode` | string | Operating mode |

### `ml_features_v3.csv` (used by the anomaly pipeline)

305 rows × 58 columns, one row per mission execution, pre-joined and feature-engineered from the raw PostgreSQL/InfluxDB sources (the upstream extraction scripts that produce this file are not part of this repo). Not committed — see `.gitignore`.

---

## Project Structure

```
AMR-Analytics-and-Anomaly-Detection/
├── run.py                              Entry point — starts Flask dashboard
├── requirements.txt                    Python dependencies
├── .env                                Secrets (NOT committed)
├── .env.example                        Template with placeholder values — commit this
├── .gitignore
├── README.md
│
├── backend/
│   ├── app.py                          Flask app factory
│   ├── config.py                       Config loaded from env vars (no hardcoded fallbacks)
│   │
│   ├── api/
│   │   └── routes.py                   REST API blueprint
│   │
│   ├── services/
│   │   ├── analytics_service.py        Core analytics engine + thread-safe in-memory cache
│   │   └── data_loader.py              Dual-DB loader (PostgreSQL + InfluxDB)
│   │
│   ├── transformers/
│   │   └── trizlabz_transformer.py     6 data transformers
│   │
│   ├── ml/                             Trained model artifacts (generated, not committed)
│   │   ├── model.pkl
│   │   ├── scaler.pkl
│   │   └── model_metadata.json
│   │
│   ├── templates/
│   │   └── dashboard.html
│   │
│   └── static/
│       ├── css/dashboard.css
│       └── js/
│           ├── chart.umd.min.js
│           └── dashboard.js
│
├── anomaly_pipeline/                   Offline anomaly detection — run via CLI
│   ├── feature_extractor.py            Shared load/filter/cap logic
│   ├── train_model.py                  Trains Isolation Forest on the 208-mission baseline
│   ├── batch_score.py                  Scores all missions, writes anomaly_scores_294.csv
│   ├── anomaly_scorer.py               Combines IF + rule-based components
│   ├── rule_based_detector.py          Rule-based error scoring + worst_label()
│   ├── explain_anomaly.py              Per-mission score explanation (CLI tool)
│   └── baseline_208_mission_ids.csv    The locked-in clean baseline mission ID list
│
├── docs/
│   ├── project_handoff_v3.md           Full project history and design decisions
│   ├── project_review_2026-08-11.md    Security/quality review notes
│   └── phase_2B_efficiency_score_status_2026-08-18.md   Efficiency score — status & pause rationale
│
└── analysis/
    ├── correlation_analysis.py
    ├── correlation_heatmap.png
    ├── correlation_matrix.csv
    ├── eda_report.pdf
    ├── eda_summary.txt
    ├── pre_training_validation.txt
    ├── flagged_pairs.csv
    └── flagged_baseline_missions.csv   Manual spot-check of flagged baseline missions
```

---

## Known Limitations

| Area | Limitation | Status |
|---|---|---|
| `battery_health` | Approximated as avg SOC (%) over the window — not true degradation health | By design (documented in `trizlabz_transformer.py`) |
| `battery_drain_pct` | Cumulative % drain, NOT kWh (conversion needs battery rated capacity, unavailable in telemetry) | By design |
| `battery_efficiency` (Phase 2B) | Blocked — no defensible normalization possible with current fleet data (1 of 3 devices has meaningful battery activity) | See `docs/phase_2B_efficiency_score_status_2026-08-18.md` |
| Isolation Forest scoring scope | Only applied to `Mar5`-pattern missions (234 of 294) — the model has no valid basis to score structurally different mission types (test/demo runs, other patterns) | By design |
| `completion_rate` (Phase 2B) | No `failed` status exists in this dataset — completion is measured against `completed + cancelled-after-start`, excluding cancellations with no fault-attribution data | Resolved, documented |
| `scheduler_executions` | Loaded from PostgreSQL but not yet surfaced in the dashboard | Planned |
| `pois.json` | Map/POI data configured but not rendered on dashboard | Planned |
| Cache TTL | In-memory cache has no time-based expiry — use `/api/cache/clear` or `?refresh=true` to force reload | Known |
| Auth | No authentication layer on any API route, including the state-mutating `/api/cache/clear` — acceptable for local/private-network use, would need auth before any real deployment | Planned |
| ML validation metrics | No precision/recall/F1 — this is unsupervised anomaly detection with no ground-truth labels. Validated instead via baseline self-scoring, threshold-sensitivity checks, and manual spot-checking of flagged missions (see `analysis/flagged_baseline_missions.csv`) | By design |