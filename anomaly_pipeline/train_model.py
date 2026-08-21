"""
train_model.py — AMR Fleet Analytics, Phase 2A

Trains the Isolation Forest half of the hybrid anomaly detection model
and saves everything batch_score.py will need to score missions later.

Data loading and feature prep are shared with batch_score.py via
feature_extractor.py — see that file for the column-verification notes.

Pipeline:
  1. Load and prepare data (feature_extractor.load_and_prepare)
  2. Fit a StandardScaler on the XCR missions that have valid telemetry
     (292 of 294 — 2 zero-telemetry TestETbLine3 missions are excluded),
     on the 7 Isolation Forest features
  3. Filter down to the 208-mission baseline (baseline_208_mission_ids.csv)
  4. Train Isolation Forest on the scaled 208-mission baseline
  5. Save model.pkl, scaler.pkl, model_metadata.json to backend/ml/

Run from the project root (same folder as the two CSVs). feature_extractor.py
must be in the same folder as this script.

Requires: pandas, numpy, scikit-learn, joblib
    pip install pandas numpy scikit-learn joblib
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from feature_extractor import load_and_prepare

# ---------------------------------------------------------------------------
# Configuration
# Feature names, caps, contamination, and thresholds are all taken directly
# from your data / handoff doc. Two things are genuinely my call, not
# specified anywhere, flagged so they're easy to change:
#   - StandardScaler as the scaler type
#   - random_state=42, for reproducible training runs
# ---------------------------------------------------------------------------

FEATURES_FILE = Path(__file__).resolve().parent.parent / "ml_features_v3.csv"
BASELINE_FILE = Path(__file__).resolve().parent / "baseline_208_mission_ids.csv"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "backend" / "ml"
EXPECTED_BASELINE_COUNT = 208
EXPECTED_SCALER_DROPPED = 2  # confirmed: 2 TestETbLine3 missions, cancelled, 0 distance_m, 0 telemetry_readings

IF_FEATURES = [
    "avg_speed_ms",
    "speed_stddev",
    "pct_time_moving",
    "distance_efficiency",
    "duration_efficiency",
    "distance_m",
    "hour_of_day",
]

FEATURE_CAPS = {
    "physical_obstruction_score": 444.60,
    "connectivity_failure_score": 22.00,
    "duration_efficiency": 14.48,
    "distance_m": 13.96,
}

CONTAMINATION = 0.15
N_ESTIMATORS = 100     # sklearn default — not specified in the handoff
RANDOM_STATE = 42      # my choice, for reproducibility — not specified in the handoff

IF_THRESHOLDS = {"normal_max": 0.55, "warning_max": 0.75}


def main():
    print("=" * 70)
    print("STEP 1 — Load and prepare data (feature_extractor.load_and_prepare)")
    print("=" * 70)
    xcr = load_and_prepare(FEATURES_FILE, FEATURE_CAPS)

    print()
    print("=" * 70)
    print("STEP 2 — Fit a scaler on XCR missions with valid telemetry (7 IF features)")
    print("=" * 70)
    has_valid_telemetry = xcr[IF_FEATURES].notnull().all(axis=1)
    n_dropped = int((~has_valid_telemetry).sum())
    dropped_ids = xcr.loc[~has_valid_telemetry, "mission_id"].tolist()
    if n_dropped:
        print(f"  Excluding {n_dropped} row(s) with missing IF feature values from the "
              f"scaler fit: {dropped_ids}")
        print("  (confirmed distance_m=0 and telemetry_readings=0 for these — real absence "
              "of telemetry, not a data-pull error; neither is in the 208 baseline)")
    if n_dropped != EXPECTED_SCALER_DROPPED:
        print(f"  WARNING — expected exactly {EXPECTED_SCALER_DROPPED} rows dropped here "
              f"(the known zero-telemetry TestETbLine3 missions), got {n_dropped}. Check "
              "dropped_ids above before trusting this scaler.")

    X_scaler_fit = xcr.loc[has_valid_telemetry, IF_FEATURES].copy()
    scaler = StandardScaler()
    scaler.fit(X_scaler_fit)
    print(f"  Fit on {len(X_scaler_fit)} rows x {len(IF_FEATURES)} features")
    print(f"  Feature means: {dict(zip(IF_FEATURES, np.round(scaler.mean_, 4)))}")

    print()
    print("=" * 70)
    print("STEP 3 — Filter down to the 208-mission baseline")
    print("=" * 70)
    baseline_ids = pd.read_csv(BASELINE_FILE)["mission_id"]
    is_baseline = xcr["mission_id"].isin(baseline_ids)
    baseline = xcr[is_baseline].copy()
    print(f"  Matched {len(baseline)} of {len(baseline_ids)} baseline IDs")
    if len(baseline) != EXPECTED_BASELINE_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_BASELINE_COUNT} baseline rows, got {len(baseline)}. "
            "Stop here and check the mission_id join before training on this."
        )

    print("  Sanity check (should be constant/zero across the baseline, since it's "
          "filtered to zero-error missions):")
    for feat in ["physical_obstruction_score", "connectivity_failure_score", "has_escalation"]:
        n_unique = baseline[feat].nunique()
        ok = "OK" if n_unique == 1 else "UNEXPECTED — check upstream data"
        print(f"    {feat}: {n_unique} unique value(s) -> {ok}")

    print()
    print("=" * 70)
    print("STEP 4 — Train Isolation Forest on the 208-mission baseline")
    print("=" * 70)
    X_baseline_scaled = scaler.transform(baseline[IF_FEATURES])

    model = IsolationForest(
        contamination=CONTAMINATION,
        n_estimators=N_ESTIMATORS,
        random_state=RANDOM_STATE,
    )
    model.fit(X_baseline_scaled)
    print(f"  Trained on {X_baseline_scaled.shape[0]} rows x {X_baseline_scaled.shape[1]} features")

    # Self-check: score the baseline against itself. Almost all 208 should
    # land "Normal" — if a large fraction don't, something upstream is off.
    if_scores = -model.score_samples(X_baseline_scaled)
    normal = int((if_scores < IF_THRESHOLDS["normal_max"]).sum())
    warning = int(((if_scores >= IF_THRESHOLDS["normal_max"]) & (if_scores < IF_THRESHOLDS["warning_max"])).sum())
    critical = int((if_scores >= IF_THRESHOLDS["warning_max"]).sum())
    print(f"  Baseline self-score distribution: Normal={normal}, Warning={warning}, "
          f"Critical={critical} (of {len(if_scores)})")
    print(f"  Score range: {if_scores.min():.4f} to {if_scores.max():.4f}, mean {if_scores.mean():.4f}")
    if critical > len(if_scores) * 0.05:
        print("  WARNING — more than 5% of the CLEAN baseline scored Critical. "
              "Double check the scaler fit (292 rows) and feature order "
              "before trusting this model.")

    print()
    print("=" * 70)
    print("STEP 5 — Save model.pkl, scaler.pkl, model_metadata.json")
    print("=" * 70)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, str(OUTPUT_DIR / "model.pkl"))
    joblib.dump(scaler, str(OUTPUT_DIR / "scaler.pkl"))

    metadata = {
        "model_version": "v1_hybrid_phase2a",
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "sklearn_version": sklearn.__version__,
        "if_features": IF_FEATURES,
        "feature_caps": FEATURE_CAPS,
        "contamination": CONTAMINATION,
        "n_estimators": N_ESTIMATORS,
        "random_state": RANDOM_STATE,
        "scaler_fit_rows": int(len(X_scaler_fit)),
        "scaler_excluded_mission_ids": dropped_ids,
        "scaler_excluded_reason": "zero telemetry_readings and zero distance_m — no speed data to fit on",
        "training_rows": int(X_baseline_scaled.shape[0]),
        "if_thresholds": IF_THRESHOLDS,
        "if_score_convention": (
            "if_score = -model.score_samples(X); higher means more anomalous "
            "(original Isolation Forest paper convention, not sklearn's sign)."
        ),
    }
    with open(OUTPUT_DIR / "model_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"  Saved model.pkl, scaler.pkl, model_metadata.json to {OUTPUT_DIR}/")
    print()
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSTOPPED — {exc}")
        sys.exit(1)