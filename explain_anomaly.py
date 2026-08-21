"""
explain_anomaly.py — AMR Fleet Analytics, Phase 2A

On-demand tool: given a single mission_id, explains why it got the
if_label / error_label / final_label it did in anomaly_scores_294.csv.

For the IF component, ranks the 7 Isolation Forest features by deviation
from the baseline population (the same 292 valid-telemetry missions the
scaler was fit on). Six features use the scaler's own mean/std, since
that's literally what scaler.transform() used to produce this mission's
if_score. duration_efficiency uses a robust median/MAD ("modified")
z-score instead: it's heavily right-skewed (r=0.997 with the dropped
duration_minutes feature, whose own distribution has median 0.76 vs
mean 17.75 on the related duration figure), so mean/std on it is
distorted by a handful of extreme long missions — a real difference
between two ordinary missions can look artificially small, and the
score becomes dominated by whatever sits in the extreme tail.

For the rule-based component, shows which specific thresholds were
crossed and by how much.

Usage:
    python explain_anomaly.py <mission_id>

Requires batch_score.py to have already run (needs anomaly_scores_294.csv,
backend/ml/scaler.pkl, and backend/ml/model_metadata.json).
"""

import json
import sys
from pathlib import Path

import joblib
import pandas as pd

from feature_extractor import load_and_prepare
from rule_based_detector import ERROR_THRESHOLDS, rule_label

MODEL_DIR = Path("backend/ml")
FEATURES_FILE = "ml_features_v3.csv"
SCORES_FILE = "anomaly_scores_294.csv"

ROBUST_FEATURES = {"duration_efficiency"}  # use median/MAD instead of mean/std
MAD_SCALE_CONSTANT = 0.6745  # makes MAD-based z comparable to a normal-distribution std


def explain(mission_id):
    with open(MODEL_DIR / "model_metadata.json") as f:
        metadata = json.load(f)
    scaler = joblib.load(str(MODEL_DIR / "scaler.pkl"))
    if_features = metadata["if_features"]

    scores = pd.read_csv(SCORES_FILE)
    row = scores[scores["mission_id"] == mission_id]
    if row.empty:
        raise ValueError(
            f"'{mission_id}' isn't in {SCORES_FILE}. Check the ID, or re-run "
            "batch_score.py if this is a newer mission that hasn't been scored yet."
        )
    row = row.iloc[0]

    print("=" * 70)
    print(f"Mission {mission_id}")
    print("=" * 70)
    print(f"  Type: {row['mission_name']}, status: {row['status']}")
    print(f"  if_label: {row['if_label']}"
          + (f" (if_score={row['if_score']:.4f})" if pd.notna(row["if_score"]) else ""))
    print(f"  error_label: {row['error_label']}")
    print(f"  final_label: {row['final_label']}")
    if pd.notna(row["notes"]) and row["notes"] != "":
        print(f"  notes: {row['notes']}")

    # ---- IF component: which features are most unusual, and why ----
    print()
    print("-" * 70)
    print("Isolation Forest component")
    print("-" * 70)
    if pd.isna(row["if_score"]):
        reason = "not a Mar5-pattern mission" if row["notes"] == "not_mar5_pattern" else "missing telemetry"
        print(f"  Not scored — {reason}. No IF-based explanation available.")
    else:
        # The same 292-row population the scaler was fit on, reconstructed
        # here (not re-fit) so the "typical" values below are read out of
        # the exact same data the model actually used.
        population = load_and_prepare(FEATURES_FILE, metadata["feature_caps"], verbose=False)
        has_telemetry = population[if_features].notnull().all(axis=1)
        population = population[has_telemetry]

        target = population[population["mission_id"] == mission_id]
        if target.empty:
            print(f"  WARNING — mission has an if_score in {SCORES_FILE} but wasn't found in "
                  "the reconstructed scaler population. Can't compute a feature breakdown.")
        else:
            mf = target.iloc[0]

            dur_eff_pop = population["duration_efficiency"]
            dur_eff_median = dur_eff_pop.median()
            dur_eff_mad = (dur_eff_pop - dur_eff_median).abs().median()

            deviations = []
            for i, feat in enumerate(if_features):
                value = mf[feat]
                if feat in ROBUST_FEATURES:
                    typical = dur_eff_median
                    z = MAD_SCALE_CONSTANT * (value - typical) / dur_eff_mad if dur_eff_mad else 0.0
                    robust = True
                else:
                    typical = scaler.mean_[i]
                    std = scaler.scale_[i]
                    z = (value - typical) / std if std else 0.0
                    robust = False
                deviations.append((feat, value, typical, z, robust))
            deviations.sort(key=lambda t: abs(t[3]), reverse=True)

            th = metadata["if_thresholds"]
            print(f"  if_score={row['if_score']:.4f}  "
                  f"(Normal < {th['normal_max']}, Warning < {th['warning_max']}, else Critical)")
            print()
            print("  Features ranked by deviation from the baseline (the 292 valid-telemetry")
            print("  missions the scaler was fit on):")
            print(f"  {'feature':<22}{'this mission':>14}{'typical':>12}{'std devs off':>14}")
            for feat, value, typical, z, robust in deviations:
                marker = "*" if robust else " "
                print(f"  {feat:<22}{value:>14.4f}{typical:>12.4f}{z:>+13.2f}{marker}")
            if any(d[4] for d in deviations):
                print("  * median/MAD-based (robust), not mean/std — see module docstring")

    # ---- Error component: which specific thresholds were crossed ----
    print()
    print("-" * 70)
    print("Rule-based error component")
    print("-" * 70)
    pos_cfg = ERROR_THRESHOLDS["physical_obstruction_score"]
    cfs_cfg = ERROR_THRESHOLDS["connectivity_failure_score"]
    print(f"  physical_obstruction_score = {row['physical_obstruction_score']:.0f}  "
          f"-> {rule_label(row['physical_obstruction_score'], pos_cfg['warning_gt'], pos_cfg['critical_gt'])} "
          f"(Warning > {pos_cfg['warning_gt']}, Critical > {pos_cfg['critical_gt']})")
    print(f"  connectivity_failure_score = {row['connectivity_failure_score']:.0f}  "
          f"-> {rule_label(row['connectivity_failure_score'], cfs_cfg['warning_gt'], cfs_cfg['critical_gt'])} "
          f"(Warning > {cfs_cfg['warning_gt']}, Critical > {cfs_cfg['critical_gt']})")
    print(f"  has_escalation = {int(row['has_escalation'])}  "
          f"-> {'Critical' if row['has_escalation'] == 1 else 'Normal'} (Critical if 1)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python explain_anomaly.py <mission_id>")
        sys.exit(1)
    try:
        explain(sys.argv[1])
    except Exception as exc:
        print(f"\nSTOPPED — {exc}")
        sys.exit(1)