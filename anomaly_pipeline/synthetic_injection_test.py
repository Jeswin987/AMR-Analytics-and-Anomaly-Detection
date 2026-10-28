"""
synthetic_injection_test.py — AMR Fleet Analytics, Phase 2A validation

Everything run so far in this project avoided one question: when the model
IS shown a clear anomaly, does it actually catch it? There has been no
ground truth to check that against.

This script creates ground truth directly: take real baseline missions,
corrupt ONE feature at a time by a controlled amount (3, 5, and 8 standard
deviations, using the scaler's own mean/std — the same std the trained
model already uses to judge "unusual"), and check whether the model,
loaded exactly as deployed (model.pkl / scaler.pkl, no retraining), flags
the corrupted mission as Warning or Critical.

Because the corruption is synthetic and known, this produces a real,
defensible detection rate — not a judgment call.

Design choices below are mine, not derived from the project's data, and
are stated as such:
  - Perturbation magnitudes: 3 / 5 / 8 standard deviations, both directions
  - Sample size: 50 randomly chosen baseline missions, fixed seed
  - One feature perturbed at a time (univariate), not combinations

Run from inside anomaly_pipeline/:
    python synthetic_injection_test.py

Writes analysis/synthetic_injection_results.csv.
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
SAMPLE_SEED = 7          # my choice, arbitrary, fixed for reproducibility
MAGNITUDES = [3, 5, 8]   # multiples of the scaler's std; my choice, not derived
DIRECTIONS = [1, -1]


def main():
    print("=" * 70)
    print("SETUP")
    print("=" * 70)
    model = joblib.load(str(MODEL_DIR / "model.pkl"))
    scaler = joblib.load(str(MODEL_DIR / "scaler.pkl"))
    with open(MODEL_DIR / "model_metadata.json") as f:
        meta = json.load(f)
    if_features = meta["if_features"]
    normal_max = meta["if_thresholds"]["normal_max"]
    warning_max = meta["if_thresholds"]["warning_max"]

    xcr = load_and_prepare(str(FEATURES_FILE), meta["feature_caps"], verbose=False)
    baseline_ids = pd.read_csv(BASELINE_FILE)["mission_id"]
    baseline = xcr[xcr["mission_id"].isin(baseline_ids)].copy().reset_index(drop=True)

    rng = np.random.default_rng(SAMPLE_SEED)
    sample_idx = rng.choice(len(baseline), size=SAMPLE_SIZE, replace=False)
    sample = baseline.iloc[sample_idx].copy().reset_index(drop=True)

    feat_mean = dict(zip(if_features, scaler.mean_))
    feat_std = dict(zip(if_features, scaler.scale_))

    print(f"  Sample: {SAMPLE_SIZE} of {len(baseline)} baseline missions, seed={SAMPLE_SEED}")
    print(f"  Magnitudes tested: {MAGNITUDES} standard deviations, both directions")
    print(f"  Scaler mean/std per feature (same values the trained model uses):")
    for f in if_features:
        print(f"    {f:<22} mean={feat_mean[f]:.4f}  std={feat_std[f]:.4f}")

    # ------------------------------------------------------------------
    # Baseline scores (unperturbed) for the sample, as a control
    # ------------------------------------------------------------------
    X_sample = scaler.transform(sample[if_features])
    sample_scores = -model.score_samples(X_sample)
    sample["original_score"] = sample_scores
    sample["original_label"] = np.select(
        [sample_scores < normal_max, sample_scores < warning_max],
        ["Normal", "Warning"], default="Critical",
    )
    n_already_flagged = int((sample_scores >= normal_max).sum())
    print()
    print(f"  Control: of these {SAMPLE_SIZE} unperturbed missions, "
          f"{n_already_flagged} were already Warning/Critical before any injection "
          f"({100*n_already_flagged/SAMPLE_SIZE:.1f}%)")

    # ------------------------------------------------------------------
    # Injection loop
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("Running injections...")
    print("=" * 70)
    rows = []
    for feat in if_features:
        for mag in MAGNITUDES:
            for direction in DIRECTIONS:
                corrupted = sample.copy()
                shift = direction * mag * feat_std[feat]
                corrupted[feat] = sample[feat] + shift
                X_corrupt = scaler.transform(corrupted[if_features])
                scores = -model.score_samples(X_corrupt)
                flagged = scores >= normal_max
                newly_flagged = flagged & (sample["original_label"] == "Normal")
                rows.append({
                    "feature": feat,
                    "magnitude_sd": mag,
                    "direction": "+" if direction > 0 else "-",
                    "n_missions": SAMPLE_SIZE,
                    "n_flagged_after": int(flagged.sum()),
                    "detection_rate_pct": round(100 * flagged.sum() / SAMPLE_SIZE, 1),
                    "n_newly_flagged": int(newly_flagged.sum()),
                    "newly_flagged_rate_pct": round(
                        100 * newly_flagged.sum() / max(1, (sample["original_label"] == "Normal").sum()), 1
                    ),
                    "mean_score_before": round(sample["original_score"].mean(), 4),
                    "mean_score_after": round(scores.mean(), 4),
                    "mean_score_shift": round(scores.mean() - sample["original_score"].mean(), 4),
                })
    results = pd.DataFrame(rows)

    print()
    print("Detection rate by feature and magnitude (both directions combined, mean):")
    pivot = results.groupby(["feature", "magnitude_sd"])["detection_rate_pct"].mean().unstack()
    print(pivot.round(1).to_string())

    print()
    print("=" * 70)
    print("Full results, sorted by detection rate (highest first)")
    print("=" * 70)
    cols = ["feature", "magnitude_sd", "direction", "detection_rate_pct",
            "newly_flagged_rate_pct", "mean_score_before", "mean_score_after", "mean_score_shift"]
    print(results.sort_values("detection_rate_pct", ascending=False)[cols].to_string(index=False))

    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    overall_detection = results["detection_rate_pct"].mean()
    at_8sd = results[results["magnitude_sd"] == 8]["detection_rate_pct"].mean()
    at_3sd = results[results["magnitude_sd"] == 3]["detection_rate_pct"].mean()
    print(f"  Mean detection rate across all features/magnitudes/directions: {overall_detection:.1f}%")
    print(f"  Mean detection rate at 3 SD (small, plausible anomaly):  {at_3sd:.1f}%")
    print(f"  Mean detection rate at 8 SD (extreme, obvious anomaly):  {at_8sd:.1f}%")
    never_detected = results[results["detection_rate_pct"] < 50]
    if not never_detected.empty:
        print(f"\n  Feature/magnitude/direction combos with <50% detection "
              f"(the model mostly misses these):")
        print(never_detected[["feature", "magnitude_sd", "direction", "detection_rate_pct"]]
              .to_string(index=False))
    else:
        print("\n  No combination had a detection rate below 50%.")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / "synthetic_injection_results.csv"
    results.to_csv(out_path, index=False)
    print(f"\nWrote full results to {out_path}")
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSTOPPED — {exc}")
        sys.exit(1)