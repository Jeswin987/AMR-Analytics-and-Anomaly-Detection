"""
sensitivity_and_multivariate_test.py — AMR Fleet Analytics, Phase 2A validation

Follow-up to synthetic_injection_test.py, which found detection rates were
identical at 3/5/8 SD for every feature — a saturation pattern that needed
explaining before the 32.3% headline number could be trusted, and a test
design (one feature at a time) that didn't match what the earlier manual
spot-check actually found: real flagged missions moved on multiple
features together (slower speed AND unusual duration), not one at a time.

This script does two things with the same sample used before (50 baseline
missions, seed=7, for direct comparability):

  PART 1 — Magnitude sweep on distance_m (the most sensitive feature from
  the first test) at 0.25, 0.5, 1, 1.5, 2, 3, 5, 8 SD, to find where
  detection actually rises and where it flattens out.

  PART 2 — Multivariate injection: avg_speed_ms decreased and
  duration_efficiency increased AT THE SAME TIME (the real pattern from
  the spot-check), compared directly against injecting each alone, at
  matched magnitudes.

Run from inside anomaly_pipeline/:
    python sensitivity_and_multivariate_test.py

Writes analysis/sensitivity_sweep.csv and analysis/multivariate_results.csv.
"""

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from feature_extractor import load_and_prepare

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FEATURES_FILE = ROOT / "ml_features_v3.csv"
BASELINE_FILE = HERE / "baseline_208_mission_ids.csv"
MODEL_DIR = ROOT / "backend" / "ml"
OUTPUT_DIR = ROOT / "analysis"

SAMPLE_SIZE = 50
SAMPLE_SEED = 7  # same as synthetic_injection_test.py, for direct comparability
SWEEP_MAGNITUDES = [0.25, 0.5, 1, 1.5, 2, 3, 5, 8]
PAIR_MAGNITUDES = [1, 2, 3, 5]


def load_common():
    model = joblib.load(str(MODEL_DIR / "model.pkl"))
    scaler = joblib.load(str(MODEL_DIR / "scaler.pkl"))
    with open(MODEL_DIR / "model_metadata.json") as f:
        meta = json.load(f)
    if_features = meta["if_features"]
    normal_max = meta["if_thresholds"]["normal_max"]

    xcr = load_and_prepare(str(FEATURES_FILE), meta["feature_caps"], verbose=False)
    baseline_ids = pd.read_csv(BASELINE_FILE)["mission_id"]
    baseline = xcr[xcr["mission_id"].isin(baseline_ids)].copy().reset_index(drop=True)

    rng = np.random.default_rng(SAMPLE_SEED)
    sample_idx = rng.choice(len(baseline), size=SAMPLE_SIZE, replace=False)
    sample = baseline.iloc[sample_idx].copy().reset_index(drop=True)

    feat_mean = dict(zip(if_features, scaler.mean_))
    feat_std = dict(zip(if_features, scaler.scale_))
    return model, scaler, if_features, normal_max, sample, feat_mean, feat_std


def score(model, scaler, if_features, df):
    X = scaler.transform(df[if_features])
    return -model.score_samples(X)


def part1_sweep(model, scaler, if_features, normal_max, sample, feat_std):
    print("=" * 70)
    print("PART 1 — Magnitude sweep on distance_m (both directions)")
    print("=" * 70)
    rows = []
    for mag in SWEEP_MAGNITUDES:
        for direction, label in [(-1, "-"), (1, "+")]:
            corrupted = sample.copy()
            corrupted["distance_m"] = sample["distance_m"] + direction * mag * feat_std["distance_m"]
            scores = score(model, scaler, if_features, corrupted)
            flagged = int((scores >= normal_max).sum())
            rows.append({
                "magnitude_sd": mag, "direction": label,
                "detection_rate_pct": round(100 * flagged / SAMPLE_SIZE, 1),
                "mean_score": round(scores.mean(), 4),
            })
    df = pd.DataFrame(rows)
    pivot = df.pivot(index="magnitude_sd", columns="direction", values="detection_rate_pct")
    print(pivot.to_string())
    print()
    print("If detection rises gradually then flattens, that confirms score saturation")
    print("at the high end, not a bug — the model can't distinguish 'very anomalous'")
    print("from 'extremely anomalous' once a point is isolated in one split.")
    print("If detection is already flat at 0.25 SD, something else needs investigating.")
    OUTPUT_DIR.mkdir(exist_ok=True)
    df.to_csv(OUTPUT_DIR / "sensitivity_sweep.csv", index=False)
    return df


def part2_multivariate(model, scaler, if_features, normal_max, sample, feat_std):
    print()
    print("=" * 70)
    print("PART 2 — Single-feature vs. matched multivariate injection")
    print("=" * 70)
    print("Pattern: avg_speed_ms DOWN and duration_efficiency UP together,")
    print("matching what the manual spot-check found in real flagged missions.")
    print()
    rows = []
    for mag in PAIR_MAGNITUDES:
        speed_only = sample.copy()
        speed_only["avg_speed_ms"] = sample["avg_speed_ms"] - mag * feat_std["avg_speed_ms"]
        speed_scores = score(model, scaler, if_features, speed_only)

        duration_only = sample.copy()
        duration_only["duration_efficiency"] = sample["duration_efficiency"] + mag * feat_std["duration_efficiency"]
        duration_scores = score(model, scaler, if_features, duration_only)

        combined = sample.copy()
        combined["avg_speed_ms"] = sample["avg_speed_ms"] - mag * feat_std["avg_speed_ms"]
        combined["duration_efficiency"] = sample["duration_efficiency"] + mag * feat_std["duration_efficiency"]
        combined_scores = score(model, scaler, if_features, combined)

        rows.append({
            "magnitude_sd": mag,
            "speed_only_detection_pct": round(100 * (speed_scores >= normal_max).sum() / SAMPLE_SIZE, 1),
            "duration_only_detection_pct": round(100 * (duration_scores >= normal_max).sum() / SAMPLE_SIZE, 1),
            "combined_detection_pct": round(100 * (combined_scores >= normal_max).sum() / SAMPLE_SIZE, 1),
            "speed_only_mean_score": round(speed_scores.mean(), 4),
            "duration_only_mean_score": round(duration_scores.mean(), 4),
            "combined_mean_score": round(combined_scores.mean(), 4),
        })
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()
    print("If combined_detection_pct is clearly higher than either single-feature")
    print("column at the same magnitude, the model is meaningfully better at catching")
    print("realistic, multi-feature anomalies than the univariate test alone suggested —")
    print("and that matters here because real flagged missions were multi-feature.")
    df.to_csv(OUTPUT_DIR / "multivariate_results.csv", index=False)
    return df


def main():
    model, scaler, if_features, normal_max, sample, feat_mean, feat_std = load_common()
    part1_sweep(model, scaler, if_features, normal_max, sample, feat_std)
    part2_multivariate(model, scaler, if_features, normal_max, sample, feat_std)
    print()
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSTOPPED — {exc}")
        sys.exit(1)