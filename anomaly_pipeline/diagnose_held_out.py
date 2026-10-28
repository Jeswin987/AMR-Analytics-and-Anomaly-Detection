"""
diagnose_held_out.py — why are these 6 missions not in the 208 baseline?

They have zero error features, same as the baseline, yet were excluded.
Compares their raw values against the baseline across a wide set of
columns to find what actually distinguishes them — a selection
criterion the baseline used beyond "zero errors," or a real gap in
what the baseline considers normal.

Run from inside anomaly_pipeline/:
    python diagnose_held_out.py
"""

import sys
from pathlib import Path

import pandas as pd

from feature_extractor import load_and_prepare

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FEATURES_FILE = ROOT / "ml_features_v3.csv"
BASELINE_FILE = HERE / "baseline_208_mission_ids.csv"

SIX_IDS = [
    "68f79d18-c388-47f6-a8ed-468b63132cf1",
    "f6b1bb92-b1a2-4d57-8de0-a7ed16ea97ed",
    "e7286d4d-2472-48d7-8846-3c2fe18571bd",
    "aef1d11b-4332-47c0-abf9-ce9d1bfc8262",
    "ece20245-1591-48e2-ad8a-ba098dbb0f15",
    "ea1e47bc-1a01-432c-a068-75228e897df6",
]

# Caps don't matter for this comparison — raw values are what's relevant.
DUMMY_CAPS = {}

COMPARE_COLS = [
    "status", "date", "duration_minutes", "distance_m",
    "completed_waypoints", "total_waypoints", "waypoint_completion_rate",
    "planned_distance_m", "estimated_duration_s", "planned_waypoints",
    "avg_speed_ms", "max_speed_ms", "speed_stddev", "pct_time_moving",
    "pct_time_idle", "telemetry_readings", "telemetry_source",
    "battery_start_pct", "battery_end_pct", "loop_iteration",
    "unique_error_types", "total_error_events",
]


def main():
    xcr = load_and_prepare(str(FEATURES_FILE), DUMMY_CAPS, verbose=False)
    baseline_ids = set(pd.read_csv(BASELINE_FILE)["mission_id"])

    six = xcr[xcr["mission_id"].isin(SIX_IDS)].copy()
    baseline = xcr[xcr["mission_id"].isin(baseline_ids)].copy()

    cols = [c for c in COMPARE_COLS if c in xcr.columns]
    missing = [c for c in COMPARE_COLS if c not in xcr.columns]
    if missing:
        print(f"(columns not found in ml_features_v3.csv, skipped: {missing})\n")

    print("=" * 70)
    print("The 6 clean held-out missions — full relevant values")
    print("=" * 70)
    print(six[["mission_id"] + cols].to_string(index=False))

    print()
    print("=" * 70)
    print("Baseline (208) — summary stats for the same columns")
    print("=" * 70)
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(baseline[c])]
    print(baseline[numeric_cols].describe().round(4).to_string())

    non_numeric = [c for c in cols if c not in numeric_cols]
    for c in non_numeric:
        print(f"\n  Baseline '{c}' value counts:")
        print(baseline[c].value_counts(dropna=False).to_string())
        print(f"\n  The 6 missions' '{c}' values:")
        print(six[c].value_counts(dropna=False).to_string())

    print()
    print("=" * 70)
    print("Flagging columns where the 6 fall clearly outside the baseline's range")
    print("=" * 70)
    for c in numeric_cols:
        lo, hi = baseline[c].min(), baseline[c].max()
        out_of_range = six[(six[c] < lo) | (six[c] > hi)]
        if not out_of_range.empty:
            print(f"  {c}: baseline range [{lo}, {hi}]")
            print(f"    out-of-range values among the 6: {out_of_range[c].tolist()} "
                  f"(mission_ids: {out_of_range['mission_id'].tolist()})")

    print()
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSTOPPED — {exc}")
        sys.exit(1)