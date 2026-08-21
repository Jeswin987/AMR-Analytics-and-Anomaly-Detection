"""
feature_extractor.py — AMR Fleet Analytics, Phase 2A

Shared data-loading and feature-preparation logic, used by both
train_model.py and batch_score.py so the two don't carry separate
(and potentially drifting) copies of the same steps.

Given ml_features_v3.csv, this:
  1. Filters to the given robot (XCR by default)
  2. Computes physical_obstruction_score and connectivity_failure_score
     from raw error-code counts (has_escalation already exists in the
     file and is used as-is, with a cross-check against its definition)
  3. Applies the given feature caps
"""

import numpy as np
import pandas as pd

XCR_ROBOT_UUID = "0f12e514-b604-4acc-99fc-a0eadb525cdb"
EXPECTED_XCR_COUNT = 294


def load_and_prepare(features_file, feature_caps, robot_uuid=XCR_ROBOT_UUID,
                      expected_count=EXPECTED_XCR_COUNT, verbose=True):
    """
    Load features_file, filter to robot_uuid, compute the composite
    rule-based features, and apply feature_caps.

    feature_caps: dict of {column_name: cap_value}. Pass the exact caps
    a trained model expects (e.g. read from its saved
    model_metadata.json) to keep training and scoring consistent —
    this function doesn't hardcode cap values itself.

    Returns the prepared DataFrame (all original columns kept, plus
    physical_obstruction_score and connectivity_failure_score).
    """
    if verbose:
        print(f"Loading {features_file} ...")
    df = pd.read_csv(features_file)
    if verbose:
        print(f"  {len(df)} rows, {len(df.columns)} columns")

    xcr = df[df["robot_uuid"] == robot_uuid].copy()
    if verbose:
        print(f"  Filtered to robot {robot_uuid}: {len(xcr)} rows")
    if expected_count is not None and len(xcr) != expected_count:
        raise ValueError(
            f"Expected {expected_count} rows for robot {robot_uuid}, got {len(xcr)}. "
            "Stop here and check the source file before using this data."
        )

    xcr["physical_obstruction_score"] = (
        xcr["sob01_count"] + xcr["cod01_count"] + xcr["oes01_count"]
    )
    xcr["connectivity_failure_score"] = (
        xcr["cns01_count"] + xcr["cae01_count"] + xcr["csmn01_count"]
    )
    if verbose:
        print(f"  physical_obstruction_score: mean={xcr['physical_obstruction_score'].mean():.2f}, "
              f"max={xcr['physical_obstruction_score'].max():.2f}")
        print(f"  connectivity_failure_score: mean={xcr['connectivity_failure_score'].mean():.2f}, "
              f"max={xcr['connectivity_failure_score'].max():.2f}")

    # has_escalation already exists in the file — used as-is, not recomputed.
    # Cross-checked here against its definition (SOB01 and OES01 both
    # occurring in the same mission) purely as a sanity check.
    expected_escalation = ((xcr["sob01_count"] > 0) & (xcr["oes01_count"] > 0)).astype(int)
    mismatches = int((expected_escalation != xcr["has_escalation"]).sum())
    if verbose:
        print(f"  has_escalation: using the existing column ({mismatches} mismatches "
              f"against sob01_count>0 & oes01_count>0, out of {len(xcr)})")
        if mismatches:
            print("  WARNING — has_escalation doesn't match the SOB01+OES01 definition "
                  "for every row. Using the file's column as-is, but worth understanding why.")

    for feature, cap in feature_caps.items():
        n_capped = int((xcr[feature] > cap).sum())
        xcr[feature] = np.minimum(xcr[feature], cap)
        if verbose:
            print(f"  Capped '{feature}' at {cap} ({n_capped} of {len(xcr)} values were above it)")

    return xcr