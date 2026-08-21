"""
anomaly_scorer.py — AMR Fleet Analytics, Phase 2A

Combines the Isolation Forest component and the rule-based detector
(rule_based_detector.py) into one final_label per mission, for a batch
of missions at once.
"""

import numpy as np
import pandas as pd

from rule_based_detector import score_errors, worst_label


def if_label_from_score(score, thresholds):
    """IF labeling: Normal/Warning/Critical from an upper-bound cutoff."""
    if score < thresholds["normal_max"]:
        return "Normal"
    if score < thresholds["warning_max"]:
        return "Warning"
    return "Critical"


def score_isolation_forest(df, model, scaler, if_features, if_thresholds,
                            mar5_only=True, verbose=True):
    """
    Scores the Isolation Forest component. Adds if_score, if_label, and
    notes columns to df.

    mar5_only=True (default) restricts scoring to mission_name=="Mar5"
    missions with valid telemetry — the population the model's training
    baseline came from. Everything else gets if_label="Not scored" and
    notes="not_mar5_pattern", since the model has no basis to judge other
    mission types fairly. Set mar5_only=False to score every mission
    with valid telemetry regardless of type.
    """
    has_valid_telemetry = df[if_features].notnull().all(axis=1)
    if mar5_only:
        is_mar5_pattern = df["mission_name"] == "Mar5"
    else:
        is_mar5_pattern = pd.Series(True, index=df.index)
    can_score = is_mar5_pattern & has_valid_telemetry

    df["if_score"] = np.nan
    df["if_label"] = "Not scored"
    df["notes"] = ""
    df.loc[~is_mar5_pattern, "notes"] = "not_mar5_pattern"
    df.loc[is_mar5_pattern & ~has_valid_telemetry, "notes"] = "insufficient_telemetry"

    X = df.loc[can_score, if_features]
    if len(X):
        X_scaled = scaler.transform(X)
        # Sign note: sklearn's score_samples() is the *negative* of the
        # anomaly score defined in the original Isolation Forest paper
        # (higher = more anomalous, bounded ~[0,1]). Negating it here
        # recovers that convention, which is what if_thresholds assumes.
        scores = -model.score_samples(X_scaled)
        df.loc[can_score, "if_score"] = scores
        df.loc[can_score, "if_label"] = [if_label_from_score(s, if_thresholds) for s in scores]

    if verbose:
        print(f"  {int(can_score.sum())} of {len(df)} missions scored by the Isolation Forest")
        print(f"  IF label distribution (scored only): "
              f"{df.loc[can_score, 'if_label'].value_counts().to_dict()}")
    return df


def score_batch(df, model, scaler, metadata, verbose=True):
    """
    Full hybrid scoring pipeline for a batch of missions already prepared
    by feature_extractor.load_and_prepare(). Returns df with if_score,
    if_label, error_label, final_label, and notes columns added.
    """
    df = score_isolation_forest(
        df, model, scaler, metadata["if_features"], metadata["if_thresholds"], verbose=verbose
    )
    df = score_errors(df, verbose=verbose)
    df["final_label"] = df.apply(lambda r: worst_label(r["if_label"], r["error_label"]), axis=1)
    if verbose:
        print(f"  Final label distribution: {df['final_label'].value_counts().to_dict()}")
    return df
