# AMR Fleet Analytics Dashboard

> **Production-grade analytics engine for Autonomous Mobile Robot (AMR) fleet operations.**  
> Real-time telemetry visualisation, mission drill-down, battery attribution, and utilization tracking.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the App](#running-the-app)
- [API Reference](#api-reference)
- [Data Sources](#data-sources)
- [Project Structure](#project-structure)
- [Known Limitations](#known-limitations)

---

## Overview

The AMR Fleet Analytics Dashboard connects to two live databases:

| Source | Technology | Data |
|---|---|---|
| Relational | **PostgreSQL** (`AMR_Prod`) | Mission executions, scheduler runs, waypoints |
| Time-series | **InfluxDB** (`Telemetry` bucket) | Battery %, speed, position, device status |

The dashboard provides:
- **Device overview** — utilization rate, working vs idle hours, task success/failure, battery SOC curve, hourly drain chart
- **Mission drill-down** — per-mission battery attribution (direct or interpolated), duration, distance, drain rate
- **Battery attribution** — 1-minute resampled, noise-filtered telemetry with time-proportional drain sharing across concurrent missions

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
cd AMR-Analytics_app_NEW_DATA

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
**Never commit `.env` to git** — it is listed in `.gitignore`.

Copy the template and fill in your values:

```bash
copy .env .env.example   # keep a template without secrets
```

**`.env` variables:**

```env
# PostgreSQL
DATABASE_URL=postgresql://postgres:password@localhost:5432/AMR_Prod
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=AMR_Prod

# InfluxDB
INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=your_influxdb_token_here
INFLUX_ORG=Trizlabz
INFLUX_BUCKET=Telemetry

# Flask
FLASK_DEBUG=false        # set to true for local development only
FLASK_HOST=0.0.0.0
FLASK_PORT=5001
```

> **Security note:** `FLASK_DEBUG=true` enables the Flask interactive debugger which can expose secret values. Always set `FLASK_DEBUG=false` in production.

---

## Running the App

```bash
# Activate venv first (if not already)
venv\Scripts\activate

# Start the server
python run.py
```

Open your browser at: **http://localhost:5001**

The server will:
1. Connect to PostgreSQL and InfluxDB on startup
2. Auto-select the first available device and load its data
3. Background-warm the cache for all discovered devices

---

## One-Time Data Migration (CSV → PostgreSQL)

If you need to seed PostgreSQL from CSV files in the `Data/` directory:

```bash
python load_csv_to_postgres.py
```

> ⚠️ This script uses `if_exists='replace'` — it will **overwrite** existing tables.  
> Change to `if_exists='append'` in the script for incremental loads.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness check — returns `{"status": "ok"}` |
| `GET` | `/api/devices` | List all known device IDs |
| `GET` | `/api/data/<device_id>` | Full dashboard payload for a device |
| `GET` | `/api/data/<device_id>?refresh=true` | Force cache bypass |
| `GET` | `/api/mission/<device_id>/<index>` | Per-mission drill-down (index = 0-based, most-recent-first) |
| `POST` | `/api/cache/clear` | Clear all cached data |
| `POST` | `/api/cache/clear?device=<id>` | Clear cache for a specific device |

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
| `status` | `text` | `COMPLETED`, `FAILED`, `CANCELLED`, `RUNNING`, `PENDING` |
| `"missionName"` | `text` | Human-readable name |
| `"distanceTravelled"` | `numeric` | Metres |
| `"completedWaypoints"` | `int` | Waypoints finished |
| `"totalWaypoints"` | `int` | Total waypoints planned |

### InfluxDB — `Telemetry` bucket, measurement `device_telemetry`

> **Important schema note:** In this bucket, `deviceId` is stored as a **field value**, not an InfluxDB tag. Flux tag-filters on `deviceId` will match nothing. All telemetry is fetched for the time window and then filtered in pandas **after** pivoting.

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

---

## Project Structure

```
AMR-Analytics_app_NEW_DATA/
├── run.py                              Entry point — starts Flask
├── load_csv_to_postgres.py             One-off CSV → PostgreSQL migration
├── requirements.txt                    Python dependencies
├── .env                                Secrets (NOT committed to git)
├── .env.example                        Template — commit this instead
├── .gitignore
├── README.md
│
└── backend/
    ├── app.py                          Flask app factory
    ├── config.py                       Config loaded from env vars
    │
    ├── api/
    │   └── routes.py                   REST API blueprint (5 routes)
    │
    ├── services/
    │   ├── analytics_service.py        Core analytics engine + in-memory cache
    │   └── data_loader.py              Dual-DB loader (PostgreSQL + InfluxDB)
    │
    ├── transformers/
    │   └── trizlabz_transformer.py     6 data transformers
    │
    ├── templates/
    │   └── dashboard.html              HTML shell (no inline CSS/JS)
    │
    └── static/
        ├── css/dashboard.css           Consolidated stylesheet
        └── js/
            ├── chart.umd.min.js        Chart.js (bundled, offline-safe)
            └── dashboard.js            All frontend logic
```

---

## Known Limitations

| Area | Limitation | Status |
|---|---|---|
| `battery_health` | Approximated as avg SOC (%) — not true degradation health | By design (insufficient data) |
| `battery_drain_pct` | Cumulative % drain, NOT kWh (conversion needs battery rated capacity) | By design |
| `scheduler_executions` | Loaded from PostgreSQL but not yet surfaced in the dashboard | Planned |
| `pois.json` | Map/POI data configured but not rendered on dashboard | Planned |
| InfluxDB devices | Device enumeration via InfluxDB is skipped (deviceId is a field not a tag) — PostgreSQL is sole source | By design |
| Cache TTL | In-memory cache has no TTL — use `/api/cache/clear` or `?refresh=true` to force reload | Known |
| Auth | No authentication layer — deploy behind a reverse proxy (nginx + auth) for production | Planned |
