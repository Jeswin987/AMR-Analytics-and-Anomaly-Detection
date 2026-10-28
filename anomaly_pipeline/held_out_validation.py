"""
held_out_validation.py — AMR Fleet Analytics, Phase 2A validation

The Isolation Forest is scored (via anomaly_scorer.py / batch_score.py) on
234 Mar5-pattern missions with valid telemetry, but only trained on 208 of
them (the "clean" baseline). This script identifies the other 26, checks
whether they were actually comparable to the baseline before training
started, and scores them with the ALREADY-TRAINED model (loaded from
backend/ml/, not retrained) to see how it behaves on missions it never
trained on.

This is not a full out-of-sample test — see the printed caveats — but it is
real evidence about the 26 missions the model has never seen, which nothing
run so far in this project has checked.

Run from inside anomaly_pipeline/:
    python held_out_validation.py

Writes analysis/held_out_scores.csv.
"""

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

from feature_extractor import load_and_prepare

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FEATURES_FILE = ROOT / "ml_features_v3.csv"
BASELINE_FILE = HERE / "baseline_208_mission_ids.csv"
MODEL_DIR = ROOT / "backend" / "ml"
OUTPUT_DIR = ROOT / "analysis"

ERROR_FEATURES = ["physical_obstruction_score", "connectivity_failure_score", "has_escalation"]


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
    has_telemetry = xcr[if_features].notnull().all(axis=1)
    is_mar5 = xcr["mission_name"] == "Mar5"
    scorable = xcr[is_mar5 & has_telemetry].copy().reset_index(drop=True)

    baseline_ids = set(pd.read_csv(BASELINE_FILE)["mission_id"])
    scorable["in_baseline"] = scorable["mission_id"].isin(baseline_ids)

    n_baseline = int(scorable["in_baseline"].sum())
    n_held_out = int((~scorable["in_baseline"]).sum())
    print(f"  Mar5-pattern missions with valid telemetry: {len(scorable)}")
    print(f"  Of these, in the 208-mission training baseline: {n_baseline}")
    print(f"  Of these, NEVER seen during training (held out): {n_held_out}")
    if n_baseline != 208:
        print(f"  WARNING — expected 208 baseline missions here, got {n_baseline}. "
              "Check baseline_208_mission_ids.csv matches ml_features_v3.csv before trusting this.")

    held_out = scorable[~scorable["in_baseline"]].copy()
    baseline = scorable[scorable["in_baseline"]].copy()

    # ------------------------------------------------------------------
    # Were the held-out missions even comparable to the baseline?
    # The baseline was selected as the zero-error subset. If the held-out
    # missions have non-zero error features, they were excluded for a
    # known reason and this is not a fair "does it generalize to normal
    # data" test — it becomes a different, narrower question. Check
    # rather than assume either way.
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("Are the held-out missions actually comparable to the baseline?")
    print("=" * 70)
    print("  Baseline error features (should be constant/zero, per train_model.py):")
    for feat in ERROR_FEATURES:
        print(f"    {feat}: {sorted(baseline[feat].unique())}")
    print("  Held-out error features:")
    for feat in ERROR_FEATURES:
        print(f"    {feat}: {sorted(held_out[feat].unique())}")
    held_out_clean = (held_out[ERROR_FEATURES[0]] == 0) & \
                      (held_out[ERROR_FEATURES[1]] == 0) & \
                      (held_out[ERROR_FEATURES[2]] == 0)
    n_clean = int(held_out_clean.sum())
    print(f"  Held-out missions with all-zero error features (same as baseline): "
          f"{n_clean} of {n_held_out}")
    if n_clean < n_held_out:
        print(f"  -> {n_held_out - n_clean} held-out mission(s) have non-zero error features. "
              "For those, being excluded from the baseline was expected, not a coincidence, "
              "and scoring them is a weaker test than 'does the model generalize to normal data.'")

    # ------------------------------------------------------------------
    # Score everything with the ACTUAL TRAINED model (no retraining)
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("Scoring with the trained model (model.pkl, no retraining)")
    print("=" * 70)
    X_baseline = scaler.transform(baseline[if_features])
    X_held_out = scaler.transform(held_out[if_features])
    baseline_scores = -model.score_samples(X_baseline)
    held_out_scores = -model.score_samples(X_held_out)

    def summarize(name, scores):
        n_normal = int((scores < normal_max).sum())
        n_warning = int(((scores >= normal_max) & (scores < warning_max)).sum())
        n_critical = int((scores >= warning_max).sum())
        print(f"  {name}: n={len(scores)}  mean={scores.mean():.4f}  median={np.median(scores):.4f}  "
              f"std={scores.std():.4f}  min={scores.min():.4f}  max={scores.max():.4f}")
        print(f"    Normal={n_normal} ({100*n_normal/len(scores):.1f}%)  "
              f"Warning={n_warning} ({100*n_warning/len(scores):.1f}%)  "
              f"Critical={n_critical} ({100*n_critical/len(scores):.1f}%)")

    summarize("Baseline (trained on these 208)", baseline_scores)
    summarize(f"Held out (never trained on, n={n_held_out})", held_out_scores)

    if n_clean > 0:
        summarize(f"Held out, error-feature-clean subset only (n={n_clean})",
                   held_out_scores[held_out_clean.to_numpy()])

    print()
    print("=" * 70)
    print("Statistical comparison — baseline self-scores vs. held-out scores")
    print("=" * 70)
    stat, p = mannwhitneyu(baseline_scores, held_out_scores, alternative="two-sided")
    print(f"  Mann-Whitney U test (distribution-free, no normality assumption)")
    print(f"  U={stat:.1f}, p={p:.4f}")
    print("  p < 0.05 -> held-out scores are statistically different from baseline self-scores")
    print("  p >= 0.05 -> no statistically detectable difference at this sample size")

    print()
    print("=" * 70)
    print("Individual held-out missions, sorted by score")
    print("=" * 70)
    held_out = held_out.copy()
    held_out["if_score"] = held_out_scores
    held_out["all_error_features_zero"] = held_out_clean.to_numpy()
    held_out["if_label"] = np.select(
        [held_out["if_score"] < normal_max, held_out["if_score"] < warning_max],
        ["Normal", "Warning"], default="Critical",
    )
    cols = ["mission_id", "mission_name", "if_score", "if_label", "all_error_features_zero"] + ERROR_FEATURES
    print(held_out.sort_values("if_score", ascending=False)[cols].round(4).to_string(index=False))

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / "held_out_scores.csv"
    held_out[cols].to_csv(out_path, index=False)
    print()
    print(f"Wrote held-out scores to {out_path}")
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSTOPPED — {exc}")
        sys.exit(1)