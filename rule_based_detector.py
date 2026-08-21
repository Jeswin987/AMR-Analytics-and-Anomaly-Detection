"""
rule_based_detector.py — AMR Fleet Analytics, Phase 2A

The rule-based half of the hybrid anomaly detector. Scores
physical_obstruction_score, connectivity_failure_score, and
has_escalation against fixed thresholds (from the project handoff doc)
and combines them into one error_label per mission.

This doesn't depend on the trained model or telemetry at all — it works
from the raw error-code counts alone, so it can label every mission,
including ones the Isolation Forest can't score.
"""

ERROR_THRESHOLDS = {
    "physical_obstruction_score": {"warning_gt": 0, "critical_gt": 100},
    "connectivity_failure_score": {"warning_gt": 0, "critical_gt": 10},
}

SEVERITY = {"Normal": 0, "Warning": 1, "Critical": 2}


def rule_label(value, warning_gt, critical_gt):
    """Critical/Warning/Normal from a lower-bound cutoff."""
    if value > critical_gt:
        return "Critical"
    if value > warning_gt:
        return "Warning"
    return "Normal"


def worst_label(*labels):
    """
    The most severe of the given labels. Ignores any that aren't a
    recognised severity (e.g. "Not scored"), and falls back to "N/A" if
    none of the inputs are recognised.
    """
    real = [l for l in labels if l in SEVERITY]
    if not real:
        return "N/A"
    return max(real, key=lambda l: SEVERITY[l])


def score_errors(df, verbose=True):
    """
    Adds obstruction_label, connectivity_label, escalation_label, and
    error_label columns to df. Requires physical_obstruction_score,
    connectivity_failure_score, and has_escalation columns to already
    be present (see feature_extractor.load_and_prepare).
    """
    obstruction_cfg = ERROR_THRESHOLDS["physical_obstruction_score"]
    connectivity_cfg = ERROR_THRESHOLDS["connectivity_failure_score"]

    df["obstruction_label"] = df["physical_obstruction_score"].apply(
        lambda v: rule_label(v, obstruction_cfg["warning_gt"], obstruction_cfg["critical_gt"])
    )
    df["connectivity_label"] = df["connectivity_failure_score"].apply(
        lambda v: rule_label(v, connectivity_cfg["warning_gt"], connectivity_cfg["critical_gt"])
    )
    df["escalation_label"] = df["has_escalation"].apply(lambda v: "Critical" if v == 1 else "Normal")
    df["error_label"] = df.apply(
        lambda r: worst_label(r["obstruction_label"], r["connectivity_label"], r["escalation_label"]),
        axis=1,
    )
    if verbose:
        print(f"  Error label distribution: {df['error_label'].value_counts().to_dict()}")
    return df