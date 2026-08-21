"""
correlation_analysis.py
========================
Checks whether the 11 candidate ML features are redundant with each other
before training the Isolation Forest model.

Two checks are run:
  1. Pearson correlation — finds pairs of features that move together
  2. VIF (Variance Inflation Factor) — finds features that are redundant
     with a combination of other features (not just one other feature)

Why this matters:
  If two features measure the same underlying thing, Isolation Forest
  counts that signal twice. The model becomes oversensitive to that
  pattern and underweights everything else. We need to catch this
  before training, not after.

Run from project root:
    python correlation_analysis.py

Requirements:
    pip install statsmodels --break-system-packages

Outputs:
    correlation_matrix.csv     full 11x11 Pearson correlation matrix
    flagged_pairs.csv          pairs with |r| >= 0.7 (need attention)
    vif_report.csv             VIF score per feature
    correlation_heatmap.png    visual heatmap of the matrix
"""

import os
import sys
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# ── Check dependencies ─────────────────────────────────────────────────────

try:
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False
    print("WARNING: statsmodels not installed. VIF check will be skipped.")
    print("Run: pip install statsmodels --break-system-packages")
    print()

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    HAS_PLOT = True
except ImportError:
    HAS_PLOT = False
    print("WARNING: matplotlib/seaborn not installed. Heatmap will be skipped.")
    print("Run: pip install matplotlib seaborn --break-system-packages")
    print()

# ── The 11 confirmed features ──────────────────────────────────────────────

FEATURES = [
    "avg_speed_ms",
    "speed_stddev",
    "pct_time_moving",
    "distance_efficiency",
    "duration_efficiency",
    "physical_obstruction_score",   # computed: sob01 + cod01 + oes01
    "connectivity_failure_score",   # computed: cns01 + cae01 + csmn01
    "has_escalation",
    "duration_minutes",
    "distance_m",
    "hour_of_day",
]

# Threshold for flagging correlated pairs
CORR_THRESHOLD = 0.7   # |r| >= this = flagged
VIF_THRESHOLD  = 5.0   # VIF >= this = concern
VIF_SERIOUS    = 10.0  # VIF >= this = serious problem

def log(msg=""): print(msg, flush=True)
def section(t): log(); log("="*65); log(f"  {t}"); log("="*65)

# ── Step 1: Load data ──────────────────────────────────────────────────────

section("STEP 1: Load data")

# Load full feature CSV
if not os.path.exists("ml_features_v3.csv"):
    log("ERROR: ml_features_v3.csv not found in project root.")
    log("Run extract_ml_features_v3.py first to generate it.")
    sys.exit(1)

df = pd.read_csv("ml_features_v3.csv")
log(f"  Loaded ml_features_v3.csv: {len(df)} rows × {len(df.columns)} columns")

# Load baseline mission IDs
if not os.path.exists("baseline_208_mission_ids.csv"):
    log("ERROR: baseline_208_mission_ids.csv not found in project root.")
    log("Run pre_training_validation.py first to generate it.")
    sys.exit(1)

baseline_ids = pd.read_csv("baseline_208_mission_ids.csv")
log(f"  Loaded baseline_208_mission_ids.csv: {len(baseline_ids)} missions")

# Filter to baseline missions only
df_baseline = df[df["mission_id"].isin(baseline_ids["mission_id"])].copy()
log(f"  Filtered to baseline missions: {len(df_baseline)} rows")

if len(df_baseline) != 208:
    log(f"  WARNING: Expected 208 baseline missions, got {len(df_baseline)}")
    log("  Continuing anyway — check baseline_208_mission_ids.csv if this is unexpected")

# ── Step 2: Compute composite features ────────────────────────────────────

section("STEP 2: Compute composite features")

df_baseline["physical_obstruction_score"] = (
    df_baseline["sob01_count"] +
    df_baseline["cod01_count"] +
    df_baseline["oes01_count"]
)
df_baseline["connectivity_failure_score"] = (
    df_baseline["cns01_count"] +
    df_baseline["cae01_count"] +
    df_baseline["csmn01_count"]
)

log(f"  physical_obstruction_score: min={df_baseline['physical_obstruction_score'].min()}, max={df_baseline['physical_obstruction_score'].max()}, mean={df_baseline['physical_obstruction_score'].mean():.4f}")
log(f"  connectivity_failure_score: min={df_baseline['connectivity_failure_score'].min()}, max={df_baseline['connectivity_failure_score'].max()}, mean={df_baseline['connectivity_failure_score'].mean():.4f}")
log(f"  Both should be 0.0 mean since baseline has zero errors ✓")

# ── Step 3: Check all 11 features exist and have no nulls ─────────────────

section("STEP 3: Check features exist and have no nulls")

missing_cols = [f for f in FEATURES if f not in df_baseline.columns]
if missing_cols:
    log(f"  ERROR: These features are missing from the CSV: {missing_cols}")
    sys.exit(1)

feature_df = df_baseline[FEATURES].copy()

log(f"  {'Feature':<35} {'Nulls':<8} {'Mean':<12} {'Std':<12} {'Min':<10} {'Max'}")
log("  " + "-"*90)
for feat in FEATURES:
    nulls = feature_df[feat].isna().sum()
    mean  = feature_df[feat].mean()
    std   = feature_df[feat].std()
    mn    = feature_df[feat].min()
    mx    = feature_df[feat].max()
    flag  = "  ← NULL PROBLEM" if nulls > 0 else ""
    log(f"  {feat:<35} {nulls:<8} {mean:<12.4f} {std:<12.4f} {mn:<10.4f} {mx:.4f}{flag}")

total_nulls = feature_df.isna().sum().sum()
if total_nulls > 0:
    log(f"\n  WARNING: {total_nulls} total nulls found. Dropping rows with nulls for analysis.")
    feature_df = feature_df.dropna()
    log(f"  Remaining rows after dropping nulls: {len(feature_df)}")
else:
    log(f"\n  ✓ Zero nulls across all 11 features")

# ── Step 4: Variance check ─────────────────────────────────────────────────

section("STEP 4: Variance check (zero-variance features are useless)")

log(f"  {'Feature':<35} {'Variance':<15} {'Status'}")
log("  " + "-"*60)
zero_var = []
for feat in FEATURES:
    var = feature_df[feat].var()
    if var < 1e-10:
        status = "✗ ZERO VARIANCE — drop this feature"
        zero_var.append(feat)
    elif var < 0.001:
        status = "⚠ Very low variance — worth checking"
    else:
        status = "✓"
    log(f"  {feat:<35} {var:<15.6f} {status}")

if zero_var:
    log(f"\n  Features with zero variance: {zero_var}")
    log("  These should be removed before training.")
else:
    log(f"\n  ✓ All features have non-zero variance")

# ── Step 5: Pearson correlation matrix ────────────────────────────────────

section("STEP 5: Pearson correlation matrix")

corr_matrix = feature_df.corr(method="pearson")
corr_matrix.to_csv("correlation_matrix.csv", float_format="%.4f")
log("  Saved: correlation_matrix.csv")

# Print the matrix
log(f"\n  Full correlation matrix:")
log("  " + "  ".join(f"{f[:8]:>10}" for f in FEATURES))
for i, feat in enumerate(FEATURES):
    row = "  ".join(f"{corr_matrix.loc[feat, f]:>10.3f}" for f in FEATURES)
    log(f"  {feat[:8]:>8}  {row}")

# ── Step 6: Flag correlated pairs ─────────────────────────────────────────

section(f"STEP 6: Flagged pairs (|r| >= {CORR_THRESHOLD})")

flagged = []
for i in range(len(FEATURES)):
    for j in range(i+1, len(FEATURES)):
        f1 = FEATURES[i]
        f2 = FEATURES[j]
        r  = corr_matrix.loc[f1, f2]
        if abs(r) >= CORR_THRESHOLD:
            flagged.append({
                "feature_1": f1,
                "feature_2": f2,
                "pearson_r": round(r, 4),
                "abs_r":     round(abs(r), 4),
                "severity":  "HIGH" if abs(r) >= 0.9 else "MODERATE",
            })

if flagged:
    flagged_df = pd.DataFrame(flagged).sort_values("abs_r", ascending=False)
    flagged_df.to_csv("flagged_pairs.csv", index=False)
    log(f"  {len(flagged)} flagged pairs found:")
    log()
    log(f"  {'Feature 1':<35} {'Feature 2':<35} {'r':>8}  {'Severity'}")
    log("  " + "-"*90)
    for _, row in flagged_df.iterrows():
        log(f"  {row['feature_1']:<35} {row['feature_2']:<35} {row['pearson_r']:>8.4f}  {row['severity']}")
    log()
    log("  Saved: flagged_pairs.csv")
    log()
    log("  What to do with flagged pairs:")
    log("  HIGH (|r| >= 0.9): Drop one of the pair — they measure almost identical things")
    log("  MODERATE (0.7-0.9): Investigate — may still carry independent signal")
else:
    pd.DataFrame(columns=["feature_1","feature_2","pearson_r","abs_r","severity"]).to_csv("flagged_pairs.csv", index=False)
    log(f"  ✓ No pairs above threshold {CORR_THRESHOLD}")
    log("  All 11 features appear sufficiently independent from each other")

# ── Step 7: VIF ───────────────────────────────────────────────────────────

section("STEP 7: VIF — Variance Inflation Factor")

if not HAS_STATSMODELS:
    log("  Skipped — statsmodels not installed")
    log("  Run: pip install statsmodels --break-system-packages")
else:
    from numpy.linalg import matrix_rank

    X = feature_df[FEATURES].values
    rank = matrix_rank(X)
    if rank < len(FEATURES):
        log(f"  WARNING: Feature matrix is not full rank ({rank} < {len(FEATURES)})")
        log("  This usually means two features are perfectly collinear")
        log("  VIF cannot be computed — check zero-variance features above")
    else:
        vif_data = []
        for i, feat in enumerate(FEATURES):
            vif = variance_inflation_factor(X, i)
            if vif >= VIF_SERIOUS:
                status = "✗ SERIOUS — consider dropping"
            elif vif >= VIF_THRESHOLD:
                status = "⚠ CONCERN — review this feature"
            else:
                status = "✓"
            vif_data.append({
                "feature":  feat,
                "vif":      round(vif, 4),
                "status":   status,
            })

        vif_df = pd.DataFrame(vif_data).sort_values("vif", ascending=False)
        vif_df.to_csv("vif_report.csv", index=False)

        log(f"  {'Feature':<35} {'VIF':>8}  {'Status'}")
        log("  " + "-"*70)
        for _, row in vif_df.iterrows():
            log(f"  {row['feature']:<35} {row['vif']:>8.4f}  {row['status']}")

        log()
        log("  VIF interpretation:")
        log("  VIF < 5   = no multicollinearity concern")
        log("  VIF 5-10  = moderate concern — investigate")
        log("  VIF > 10  = serious problem — drop or combine this feature")
        log()
        log("  Saved: vif_report.csv")

# ── Step 8: Heatmap ───────────────────────────────────────────────────────

section("STEP 8: Correlation heatmap")

if not HAS_PLOT:
    log("  Skipped — matplotlib/seaborn not installed")
else:
    fig, ax = plt.subplots(figsize=(14, 11))

    # Shorten feature names for display
    short_names = {
        "avg_speed_ms":                "avg_speed",
        "speed_stddev":                "speed_std",
        "pct_time_moving":             "pct_moving",
        "distance_efficiency":         "dist_eff",
        "duration_efficiency":         "dur_eff",
        "physical_obstruction_score":  "phys_obst",
        "connectivity_failure_score":  "conn_fail",
        "has_escalation":              "escalation",
        "duration_minutes":            "duration",
        "distance_m":                  "distance",
        "hour_of_day":                 "hour",
    }
    display_corr = corr_matrix.rename(
        index=short_names, columns=short_names
    )

    mask = np.zeros_like(display_corr, dtype=bool)
    mask[np.triu_indices_from(mask)] = True

    sns.heatmap(
        display_corr,
        mask=mask,
        annot=True,
        fmt=".2f",
        cmap="RdYlGn",
        center=0,
        vmin=-1,
        vmax=1,
        linewidths=0.5,
        ax=ax,
        cbar_kws={"shrink": 0.8},
        annot_kws={"size": 10},
    )

    ax.set_title(
        "Feature Correlation Matrix — 208 Baseline Missions\n"
        "Green = positive correlation, Red = negative correlation",
        fontsize=13, pad=15
    )
    plt.xticks(rotation=45, ha="right", fontsize=11)
    plt.yticks(rotation=0, fontsize=11)
    plt.tight_layout()
    plt.savefig("correlation_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close()
    log("  Saved: correlation_heatmap.png")

# ── Step 9: Summary and recommendation ────────────────────────────────────

section("STEP 9: Summary and recommendation")

log("  Files produced:")
log("    correlation_matrix.csv   — full 11×11 matrix")
log("    flagged_pairs.csv        — pairs with |r| >= 0.7")
log("    vif_report.csv           — VIF per feature")
if HAS_PLOT:
    log("    correlation_heatmap.png  — visual heatmap")

log()
if flagged:
    log(f"  ACTION REQUIRED: {len(flagged)} correlated pair(s) found")
    log("  Paste the output above into your Claude chat.")
    log("  Claude will tell you exactly which features to drop or keep.")
else:
    log("  ✓ No action required — all features appear independent")
    log("  Ready to proceed to train_isolation_forest.py")

log()
log("  Paste this entire output into your Claude chat before doing anything else.")
log()
log("Done.")