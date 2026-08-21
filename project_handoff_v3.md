# AMR Fleet Analytics — Project Handoff v3
**Paste this as the first message in a new conversation.**
**This replaces all previous handoff documents.**

---

## Who I Am

I am Jesvin Devasia, MSc Computer Science (Data Analytics) graduate from Digital University Kerala (Register Number: 243019). I completed my internship at Trizlabz building a fleet analytics dashboard for Autonomous Mobile Robots. The internship is over and I have graduated.

Me and a friend are continuing this project independently. We have no team guidance. We are building this for our resumes. Quality must be production grade.

**Claude is our only technical guide.**

---

## How We Work

- I cannot write or debug code. Claude writes all scripts. I run them and paste terminal output back.
- Me and my friend use a shared folder for the same codebase on different machines.
- My friend works independently — no need to coordinate with him.
- We are on the Claude free plan — keep responses focused, no repetition.
- Never guess. Never use unexplained jargon. State uncertainty clearly.
- Every number must be verified from actual data before being accepted.
- Production grade quality — clean, well-commented, properly structured.
- Tell me what to do, why, and what can go wrong.

---

## Project Phases

**Phase 1 — Complete**
Web dashboard integrating PostgreSQL + InfluxDB. Three tabs: Utilisation, Task Performance, Battery and Energy. Flask REST API backend, vanilla JavaScript + Chart.js frontend.

**Phase 2A — Currently Building**
Anomaly detection using a hybrid model (Isolation Forest + rule-based error detector). Scores every Mar5 mission as Normal / Warning / Critical with explanation of what was unusual.

**Phase 2B — After 2A**
Per-robot efficiency score 0-100 for any time period.

**Phase 3 — After 2B**
Route risk analysis, time risk analysis, operational recommendations panel. Forecasting based on external variables (traffic, weather, payload) is NOT possible — that data does not exist in our databases.

---

## Environment

```
OS: Windows
Project folder: C:\Users\VICTUS\AMR-Analytics_app_NEW_DATA\
Python: 3.12 with venv
PostgreSQL: localhost:5432, database AMR_Prod, user postgres, password root
InfluxDB: localhost:8086, org trizlabz, bucket device_core, version 2.8.0 OSS
Flask port: 5001
Scripts run from project root with: python script_name.py
All scripts use .env file for credentials
```

---

## The Two Databases

### PostgreSQL
- Stores mission records
- Key table: `mission_executions` — 882 rows total
- Other tables: `mission_path` (305 rows), `scheduler_executions` (61), `scheduler_queue` (61), `vda5050_command_trackers` (70)
- Status values: `completed`, `cancelled`, `pending` — NO `failed` status exists in this data

### InfluxDB
- Stores robot telemetry — one snapshot every ~1.1 seconds
- Bucket: device_core, Measurement: device_telemetry
- 19 fields per snapshot: battery, speed, driving, operatingMode, status, errors, position, pathName, lastNodeId, orderId, zoneId, and others
- Total: 151,527 timestamps across all 3 robots

---

## The Three Robots

| Robot Name | UUID starts with | Missions | ML Usable |
|---|---|---|---|
| AMR12601071239XCR | 0f12e514 | 294 | Yes — only active robot |
| AMR12512170906BGI | 421ec268 | 7 | No — all cancelled |
| AMR12512300747UGW | f1f193bb | 4 | No — all cancelled |

---

## The 882 Missions

- 577 — never started. Batch-created Mar 5 as scheduling test. No telemetry. Excluded.
- 38 — pending status. No timestamps. Excluded.
- 305 — actually ran (have both startedAt and completedAt).
- 294 of those 305 belong to AMR12601071239XCR (the only robot with real data).

## The 294 XCR Missions

| Mission Type | Count | Avg Duration | Avg Errors | Completion Rate |
|---|---|---|---|---|
| Mar5 | 234 | 17.8 min | 38.9 | 95.3% |
| DemoMission | 37 | 1.3 min | 10.5 | 97.3% |
| TestETbLine3 | 17 | 5.2 min | 187.9 | 23.5% |
| Test | 6 | 1432 min | 114.7 | 0% |

- 263 completed, 31 cancelled — 89.5% completion rate
- 18.0% of missions have at least one error
- 9.2% have escalation (SOB01 led to OES01)
- Robot operated on 6 dates only: Feb 26, Feb 27, Mar 5, Mar 6, Mar 9, Mar 10

---

## Key Verified Numbers

| Number | Value |
|---|---|
| Total mission rows PostgreSQL | 882 |
| Missions that actually ran | 305 |
| XCR usable missions | 294 |
| XCR completed | 263 |
| XCR cancelled | 31 |
| Baseline training missions | 208 |
| InfluxDB timestamps (XCR) | 127,056 |
| InfluxDB timestamps (all) | 151,527 |
| Fields per timestamp | 19 |
| Telemetry interval | ~1.1 seconds |
| Operational dates (XCR) | 6 |
| Battery health shown in dashboard | 53.44% (average SOC — not degradation) |
| Charge cycles detected | 2 |
| Mission completion rate | 89.5% |
| Error rate | 18.0% |
| Escalation rate | 9.2% |
| Duration efficiency baseline mean | 1.82 |
| Distance efficiency baseline mean | 0.9651 |
| Avg speed baseline | 0.2765 m/s |
| Pct time moving baseline | 89.29% |
| PostgreSQL total size | 2.95 MB |
| InfluxDB estimated size | ~150-300 MB |
| Efficiency score Mar 5 POC | 65.39/100 (Good) |

---

## Known Data Issues (All Discovered and Fixed)

**Battery sensor noise**
Raw spikes from 38% to 100% in 2 seconds — impossible.
Fix: 1-minute median resampling (505,344 raw records → 482 clean points) + rate-of-change filter (>2%/min removed).

**Battery sensor malfunction Mar 6 afternoon**
Battery reports 0% for 80+ consecutive missions from ~15:36 IST. Sensor fault confirmed.
Decision: Exclude ALL battery features from ML model.

**missionId is a template ID not a unique execution ID**
Direct join produces 171,241 wrong matches.
Fix: Timestamp-based join — deviceId + startedAt within ±5 seconds. Confirmed unambiguous.

**Timezone mismatch**
PostgreSQL stores IST (+05:30). InfluxDB requires UTC.
Fix: Always convert PostgreSQL timestamps to UTC before InfluxDB queries.

**InfluxDB pivot() drops data silently**
Fix: Query each field separately, merge in pandas.

**Speed features missing for short missions**
Fix: Load full day telemetry for the device, then slice to mission window in pandas. Never query InfluxDB per mission — always query per day.

**Error code definitions unverified**
SOB01 and OES01 confirmed from internal documents.
COD01, CNS01, CAE01, CSMN01, OLMT01, ORMT01, CESP01, CMN01 — team confirmed Gemini definitions are wrong. Correct definitions never arrived. Groupings are based on co-occurrence patterns only.

---

## The 208 Baseline Missions

File: `baseline_208_mission_ids.csv` (exists in project root)
Contains: mission_id, robot_uuid, date, started_at, completed_at, status, mission_name, total_error_events

Filter that produces exactly 208 (verified):
- mission_name == "Mar5"
- status == "completed"
- total_error_events == 0

Date split: 83 from Mar 5, 125 from Mar 6.
This is the authoritative training data source.

---

## Error Code Co-occurrence Patterns (Data Facts)

- SOB01 escalates to OES01 in 27/48 missions (56%)
- COD01 always appears with CNS01 (12/12 missions)
- CNS01 appears with CAE01 in 13/14 missions
- OLMT01 and ORMT01 always appear together (1 mission only — too rare for ML)

Composite groupings (based on co-occurrence, not confirmed meanings):
- physical_obstruction_score = SOB01 + COD01 + OES01
- connectivity_failure_score = CNS01 + CAE01 + CSMN01

---

## Correlation Analysis Results (Verified — Ran Against Actual Data)

Script: `correlation_analysis.py` — already run and verified.

### Finding 1 — Three features have zero variance in the 208 baseline
```
physical_obstruction_score: ZERO VARIANCE
connectivity_failure_score: ZERO VARIANCE
has_escalation:             ZERO VARIANCE
```
Why: The 208 baseline missions were filtered to zero errors. So all error features are 0 for every training mission. Min = 0, Max = 0.

Why this matters: Isolation Forest builds trees by randomly splitting on feature ranges. If min = max = 0, no split is possible. The algorithm skips these features entirely during training AND scoring. A new mission with 847 obstacle errors would score identically to a clean mission — the model is completely blind to error events.

Solution: These three features are handled by a separate rule-based detector, not by Isolation Forest.

### Finding 2 — 10 correlated pairs found

| Feature 1 | Feature 2 | r | Action |
|---|---|---|---|
| duration_efficiency | duration_minutes | 0.9972 | DROP duration_minutes |
| avg_speed_ms | duration_minutes | -0.9780 | duration_minutes dropped |
| avg_speed_ms | duration_efficiency | -0.9747 | Keep both — diverge in anomalies |
| avg_speed_ms | speed_stddev | -0.9110 | Keep both — different signals |
| duration_minutes | distance_m | 0.9032 | duration_minutes dropped |
| duration_efficiency | distance_m | 0.8915 | Keep both — different signals |
| avg_speed_ms | distance_m | -0.8448 | Keep both |
| distance_efficiency | distance_m | 0.8429 | Keep both |
| speed_stddev | duration_efficiency | 0.8398 | Keep both |
| speed_stddev | duration_minutes | 0.8356 | duration_minutes dropped |

Only `duration_minutes` is dropped. It has r = 0.9972 with `duration_efficiency` — they are virtually identical signals on this fixed-route dataset.

The other correlations are partly an artifact of the homogeneous baseline (same route, same clean conditions). In anomalous missions these features diverge meaningfully.

### Finding 3 — VIF could not be computed
The feature matrix is not full rank because of the 3 zero-variance features. VIF is not needed — the zero-variance finding already explains the rank deficiency and the decision is clear.

---

## The Hybrid Model Architecture (Final and Confirmed)

This replaces the single Isolation Forest approach. Two components work together.

### Why hybrid?
Isolation Forest cannot detect error-based anomalies (features are all zero in training). Rule-based detection cannot detect subtle pattern anomalies (slow speed, unusual distance, unexpected timing). Together they cover both types of anomaly.

### Component 1 — Isolation Forest (7 features)
Detects unusual mission patterns in speed, movement, efficiency, distance, and timing.

**Features (confirmed — verified from actual data):**

| # | Feature | Source | Baseline Average |
|---|---|---|---|
| 1 | avg_speed_ms | InfluxDB | 0.2765 |
| 2 | speed_stddev | InfluxDB | 0.1891 |
| 3 | pct_time_moving | InfluxDB | 89.29% |
| 4 | distance_efficiency | mission_path ÷ mission_executions | 0.9651 |
| 5 | duration_efficiency | mission_path ÷ mission_executions | 1.8219 |
| 6 | distance_m | PostgreSQL | 9.9983 |
| 7 | hour_of_day | PostgreSQL | 15.88 |

**Scaler rule:** Fit scaler on ALL 294 XCR missions (with caps applied). NOT the 208 baseline. This ensures the scaler knows the full real-world range of every feature.

**Training rule:** Isolation Forest trains on 208 baseline missions only.

**Contamination:** 0.15 (anchored to 18% error rate in actual data)

**IF score → IF label:**
- 0.00 – 0.55 → Normal
- 0.55 – 0.75 → Warning
- 0.75 – 1.00 → Critical

### Component 2 — Rule-based error detector (3 features)
Detects any mission that had obstacle events, connectivity failures, or escalations. The entire 208 baseline has zero of all three — any non-zero value is by definition abnormal.

| Feature | Computed as | Warning threshold | Critical threshold |
|---|---|---|---|
| physical_obstruction_score | sob01 + cod01 + oes01 | > 0 | > 100 |
| connectivity_failure_score | cns01 + cae01 + csmn01 | > 0 | > 10 |
| has_escalation | 1 if SOB01 and OES01 both in same mission | — | == 1 (always Critical) |

**Error label logic:**
```
if has_escalation == 1:
    error_label = Critical
elif physical_obstruction_score > 100 or connectivity_failure_score > 10:
    error_label = Critical
elif physical_obstruction_score > 0 or connectivity_failure_score > 0:
    error_label = Warning
else:
    error_label = Normal
```

### Combining both components — Final label

```
Final label = worst severity of (IF label, error label)

Severity ranking: Normal < Warning < Critical

Examples:
  IF = Normal,   error = Warning  → Final = Warning
  IF = Warning,  error = Normal   → Final = Warning
  IF = Normal,   error = Critical → Final = Critical
  IF = Critical, error = Normal   → Final = Critical
  IF = Critical, error = Critical → Final = Critical
```

---

## Feature Caps (Apply to ALL 294 missions before scaler fitting)

Computed at p99, Test missions excluded, Mar5 p99 used for distance_m:

| Feature | Cap Value |
|---|---|
| physical_obstruction_score | 444.60 |
| connectivity_failure_score | 22.00 |
| duration_efficiency | 14.48 |
| distance_m | 13.96 |

Note: duration_minutes is dropped so its cap (8.54) is no longer needed.
These caps must be saved in model_metadata.json and applied identically in training and scoring.

---

## Features Dropped and Why

| Feature | Reason |
|---|---|
| duration_minutes | r = 0.9972 with duration_efficiency — virtually identical signal |
| max_speed_ms | Baseline mean exactly 0.5000 — robot hits speed limit every clean mission, zero variance signal |
| avg_battery_pct | Sensor malfunction Mar 6 afternoon (0% readings for 80+ missions) |
| day_of_week | Only Thursday and Friday in baseline — model never sees other days |
| pct_time_automatic | 100% for 292/294 missions — zero variance |
| pct_time_warning | Exact mirror of error counts — redundant |
| waypoint_completion_rate | 100% for all 208 baseline missions — zero variance in training |
| zoneId | Always "default" — zero variance |
| Individual error codes | Replaced by composite scores |
| olmt01_count, ormt01_count | Only 1 mission each — too rare |

---

## New Database Table Needed

```sql
CREATE TABLE mission_anomaly_scores (
    id                      SERIAL PRIMARY KEY,
    mission_id              TEXT NOT NULL,
    device_id               TEXT NOT NULL,
    mission_name            TEXT,
    if_score                FLOAT,
    if_label                TEXT,
    error_label             TEXT,
    final_label             TEXT,
    model_version           TEXT,
    scored_at               TIMESTAMPTZ DEFAULT NOW(),
    if_features_used        JSONB,
    error_features_used     JSONB,
    baseline_comparison     JSONB,
    flagged_features        TEXT[]
);
```

Note: Storing if_score, if_label, error_label, and final_label separately makes it easier to debug and tune thresholds later.

---

## New Files to Build (None Exist Yet)

```
backend/ml/
    train_model.py              ← BUILD THIS FIRST
    batch_score.py              ← BUILD THIS SECOND
    feature_extractor.py        ← Week 2
    rule_based_detector.py      ← Week 2
    anomaly_scorer.py           ← Week 2 (combines IF + rules)
    explain_anomaly.py          ← Week 3
    model.pkl                   ← generated by train_model.py
    scaler.pkl                  ← generated by train_model.py
    model_metadata.json         ← generated by train_model.py
backend/services/
    anomaly_service.py          ← Week 3
```

---

## What Exists Already (Project Root)

| File | Status |
|---|---|
| ml_features_v3.csv | Ready — 305 rows × 58 columns |
| baseline_208_mission_ids.csv | Ready — 208 authoritative training missions |
| correlation_matrix.csv | Ready — produced by correlation_analysis.py |
| flagged_pairs.csv | Ready — 10 flagged pairs documented |
| correlation_heatmap.png | Ready — visual heatmap |
| extract_ml_features_v3.py | Ready — regenerates ml_features_v3.csv |
| correlation_analysis.py | Ready — already run and verified |
| pre_training_validation.py | Ready |
| efficiency_score_poc.py | Ready |

---

## What We Are Building Right Now

**Week 1 — Train and validate the model**

Step 1 — Build `train_model.py`
- Load ml_features_v3.csv and baseline_208_mission_ids.csv
- Compute composite features for all 294 XCR missions
- Apply feature caps to all 294 missions
- Fit scaler on all 294 missions (the 7 IF features only)
- Filter to 208 baseline missions
- Train Isolation Forest on 208 baseline × 7 features
- Save model.pkl, scaler.pkl, model_metadata.json

Step 2 — Build `batch_score.py`
- Load saved model.pkl, scaler.pkl, model_metadata.json
- Score all 294 XCR missions
- Apply IF scoring (7 features)
- Apply rule-based error detection (3 features)
- Combine into final label
- Output anomaly_scores_294.csv with: mission_id, mission_name, status, total_error_events, if_score, if_label, error_label, final_label, all 10 feature values

Step 3 — Review results (you do this manually)
- Do Critical final labels match high-error missions?
- Do Normal final labels match clean zero-error missions?
- Do Warning labels make sense?
- If yes — model is working. Move to Week 2.
- If no — paste the output into Claude and diagnose before continuing.

**Start by building train_model.py.**

---

## The Efficiency Score (Phase 2B — After Anomaly Detection Working)

Four parameters, score 0-100, per robot, any time period.

Formula (equal weights — pending refinement):
```
score = (
    0.25 × active_ratio +
    0.25 × (1 - idle_ratio) +
    0.25 × completion_rate +
    0.25 × battery_efficiency
) × 100
```

Mar 5 proof of concept: 65.39/100 (Good)
- Active: 68.59 min (58.94%)
- Idle: 47.77 min (41.06%)
- Completion rate: 93.68%
- Battery: used neutral 0.5 (InfluxDB offline during test)

Idle time = gaps between consecutive missions only. Does not include before first or after last mission.

---

*End of handoff. Start by building train_model.py.*
