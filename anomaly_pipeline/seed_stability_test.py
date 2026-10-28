"""
seed_stability_test.py — AMR Fleet Analytics, Phase 2A validation

Two label-free checks on the Isolation Forest, using the exact trained
configuration (features, caps, thresholds, n_estimators, contamination)
read from backend/ml/model_metadata.json:

  TEST A — Seed stability
    Retrain the model on the same 208-mission baseline with different
    random seeds. If the same missions are flagged regardless of seed,
    the flags reflect real structure in the data, not one lucky random
    tree construction.

  TEST B — Does contamination affect the scores?
    Retrain with different contamination values at the same seed and
    compare scores. Our pipeline labels missions from -score_samples()
    with fixed thresholds, so this checks whether contamination=0.15
    actually influences any label.

Run from inside anomaly_pipeline/:
    python seed_stability_test.py

Writes analysis/seed_stability_by_mission.csv.
"""

import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from scipy.stats import spearmanr
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from feature_extractor import load_and_prepare

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FEATURES_FILE = ROOT / "ml_features_v3.csv"
BASELINE_FILE = HERE / "baseline_208_mission_ids.csv"
METADATA_FILE = ROOT / "backend" / "ml" / "model_metadata.json"
OUTPUT_DIR = ROOT / "analysis"

EXPECTED_BASELINE_COUNT = 208
# Seed-42 label counts printed by train_model.py earlier in this project
# (Normal=193, Warning=14, Critical=1). Used only as a reproduction check.
EXPECTED_SEED42_COUNTS = (193, 14, 1)


def label_counts(scores, normal_max, warning_max):
    n_normal = int((scores < normal_max).sum())
    n_critical = int((scores >= warning_max).sum())
    n_warning = len(scores) - n_normal - n_critical
    return n_normal, n_warning, n_critical


def main():
    with open(METADATA_FILE) as f:
        meta = json.load(f)
    if_features = meta["if_features"]
    normal_max = meta["if_thresholds"]["normal_max"]
    warning_max = meta["if_thresholds"]["warning_max"]
    contamination = meta["contamination"]
    n_estimators = meta["n_estimators"]
    ref_seed = meta["random_state"]
    seeds = sorted(set(range(10)) | {ref_seed})

    print("=" * 70)
    print("SETUP")
    print("=" * 70)
    xcr = load_and_prepare(str(FEATURES_FILE), meta["feature_caps"], verbose=False)

    # Scaler fit exactly as in train_model.py: all XCR missions with valid
    # telemetry (292 rows). Deterministic — no randomness involved.
    has_telemetry = xcr[if_features].notnull().all(axis=1)
    scaler = StandardScaler().fit(xcr.loc[has_telemetry, if_features])

    baseline_ids = pd.read_csv(BASELINE_FILE)["mission_id"]
    baseline = xcr[xcr["mission_id"].isin(baseline_ids)].copy().reset_index(drop=True)
    if len(baseline) != EXPECTED_BASELINE_COUNT:
        raise ValueError(f"Expected {EXPECTED_BASELINE_COUNT} baseline rows, got {len(baseline)}.")
    X = scaler.transform(baseline[if_features])

    print(f"  scikit-learn {sklearn.__version__}")
    print(f"  Baseline missions: {len(baseline)}   Features: {len(if_features)}")
    print(f"  Thresholds: Normal < {normal_max} <= Warning < {warning_max} <= Critical")
    print(f"  Trained config: contamination={contamination}, n_estimators={n_estimators}, "
          f"reference seed={ref_seed}")
    print(f"  Seeds tested: {seeds}")

    # ------------------------------------------------------------------
    # TEST A — seed stability
    # ------------------------------------------------------------------
    score_cols = {}
    ref_model = None
    for seed in seeds:
        model = IsolationForest(
            contamination=contamination, n_estimators=n_estimators, random_state=seed
        )
        model.fit(X)
        score_cols[seed] = -model.score_samples(X)
        if seed == ref_seed:
            ref_model = model
    scores = pd.DataFrame(score_cols, index=baseline["mission_id"])
    flags = scores >= normal_max  # Warning or Critical, same rule as production
    n_seeds = len(seeds)

    print()
    print("=" * 70)
    print("TEST A1 — Label counts per seed (baseline scored against itself)")
    print("=" * 70)
    print(f"  {'seed':>5} {'Normal':>8} {'Warning':>9} {'Critical':>9} {'Flagged':>9}")
    for seed in seeds:
        n_norm, n_warn, n_crit = label_counts(scores[seed], normal_max, warning_max)
        print(f"  {seed:>5} {n_norm:>8} {n_warn:>9} {n_crit:>9} {n_warn + n_crit:>9}")

    print()
    print("=" * 70)
    print("TEST A2 — Reproduction check (does this script match the trained model?)")
    print("=" * 70)
    got = label_counts(scores[ref_seed], normal_max, warning_max)
    print(f"  Seed {ref_seed} here:      Normal={got[0]}, Warning={got[1]}, Critical={got[2]}")
    print(f"  train_model.py output:  Normal={EXPECTED_SEED42_COUNTS[0]}, "
          f"Warning={EXPECTED_SEED42_COUNTS[1]}, Critical={EXPECTED_SEED42_COUNTS[2]}")
    print("  MATCH" if got == EXPECTED_SEED42_COUNTS else
          "  MISMATCH — do not trust the rest of this output until this is explained")

    print()
    print("=" * 70)
    print("TEST A3 — Agreement between seeds (all pairs)")
    print("=" * 70)
    rank_corrs, jaccards = [], []
    for a, b in combinations(seeds, 2):
        rho, _ = spearmanr(scores[a], scores[b])
        rank_corrs.append(rho)
        fa = set(scores.index[flags[a].to_numpy()])
        fb = set(scores.index[flags[b].to_numpy()])
        union = fa | fb
        jaccards.append(len(fa & fb) / len(union) if union else 1.0)
    print(f"  Seed pairs compared: {len(rank_corrs)}")
    print(f"  Spearman rank correlation of scores: mean={np.mean(rank_corrs):.4f}, "
          f"min={np.min(rank_corrs):.4f}")
    print(f"  Jaccard overlap of flagged sets:     mean={np.mean(jaccards):.4f}, "
          f"min={np.min(jaccards):.4f}")
    print("  (1.0 = identical ordering / identical flagged set)")

    summary = pd.DataFrame(
        {
            "flag_count": flags.sum(axis=1),
            "mean_score": scores.mean(axis=1),
            "std_score": scores.std(axis=1),
            "min_score": scores.min(axis=1),
            "max_score": scores.max(axis=1),
        }
    )
    summary["stability"] = np.select(
        [summary["flag_count"] == n_seeds, summary["flag_count"] == 0],
        ["always_flagged", "never_flagged"],
        default="unstable",
    )
    noise = summary["std_score"].median()
    summary["dist_to_threshold"] = np.minimum(
        (summary["mean_score"] - normal_max).abs(),
        (summary["mean_score"] - warning_max).abs(),
    )
    # Borderline = mean score within 2x the typical seed-to-seed score noise
    # of a threshold. The 2x multiplier is my choice, not derived.
    summary["borderline"] = summary["dist_to_threshold"] <= 2 * noise

    print()
    print("=" * 70)
    print(f"TEST A4 — Per-mission stability across {n_seeds} seeds")
    print("=" * 70)
    print(summary["stability"].value_counts().to_string())
    print(f"  Typical (median) seed-to-seed score std per mission: {noise:.4f}")

    print()
    print("=" * 70)
    print(f"TEST A5 — The {int(flags[ref_seed].sum())} missions flagged by the trained model "
          f"(seed {ref_seed}): how many seeds also flag them?")
    print("=" * 70)
    ref_flagged = summary.index[flags[ref_seed].to_numpy()]
    dist = summary.loc[ref_flagged, "flag_count"].value_counts().sort_index(ascending=False)
    print(f"  {'flagged in N of ' + str(n_seeds) + ' seeds':<28} missions")
    for count, n_missions in dist.items():
        print(f"  {count:<28} {n_missions}")

    print()
    print("=" * 70)
    print("TEST A6 — Unstable missions (flagged under some seeds, not others)")
    print("=" * 70)
    unstable = summary[summary["stability"] == "unstable"].sort_values(
        "flag_count", ascending=False
    )
    if unstable.empty:
        print("  None — every mission is either always or never flagged.")
    else:
        cols = ["flag_count", "mean_score", "std_score", "min_score", "max_score",
                "dist_to_threshold", "borderline"]
        print(unstable[cols].round(4).to_string())
        n_unstable = len(unstable)
        n_border = int(unstable["borderline"].sum())
        print(f"\n  {n_unstable} unstable missions; {n_border} are borderline "
              f"(within 2x noise of a threshold), {n_unstable - n_border} are not.")

    print()
    print("=" * 70)
    print("TEST A7 — Five highest-scoring missions")
    print("=" * 70)
    top = summary.sort_values("mean_score", ascending=False).head(5)
    print(top[["flag_count", "mean_score", "std_score", "min_score", "max_score"]]
          .round(4).to_string())

    print()
    print("=" * 70)
    print("TEST A8 — Missions reaching Critical (score >= warning_max) under any seed")
    print("=" * 70)
    crit_counts = (scores >= warning_max).sum(axis=1)
    crit_counts = crit_counts[crit_counts > 0].sort_values(ascending=False)
    if crit_counts.empty:
        print("  None.")
    else:
        for mission_id, c in crit_counts.items():
            print(f"  {mission_id}: Critical in {c} of {n_seeds} seeds")

    # ------------------------------------------------------------------
    # TEST B — does contamination change the scores?
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("TEST B — Does the contamination parameter change the scores?")
    print("=" * 70)
    print(f"  Reference model: max_samples_={ref_model.max_samples_}, "
          f"bootstrap={ref_model.bootstrap}, n_estimators={ref_model.n_estimators}")
    variants = {
        f"{contamination} (trained)": contamination,
        "auto": "auto",
        "0.05": 0.05,
        "0.30": 0.30,
    }
    variant_scores = {}
    for label, c in variants.items():
        m = IsolationForest(contamination=c, n_estimators=n_estimators, random_state=ref_seed)
        m.fit(X)
        variant_scores[label] = -m.score_samples(X)
    base_label = f"{contamination} (trained)"
    base = variant_scores[base_label]
    print(f"  Same seed ({ref_seed}), same data; max |score difference| vs the trained setting:")
    for label, sc in variant_scores.items():
        print(f"    contamination={label:<16} max abs diff = {np.max(np.abs(sc - base)):.3e}")
    print("  (0.000e+00 means contamination has no effect on score_samples)")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out = summary.copy()
    for seed in seeds:
        out[f"score_seed_{seed}"] = scores[seed]
    out_path = OUTPUT_DIR / "seed_stability_by_mission.csv"
    out.to_csv(out_path)
    print()
    print(f"Wrote per-mission results to {out_path}")
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSTOPPED — {exc}")
        sys.exit(1)