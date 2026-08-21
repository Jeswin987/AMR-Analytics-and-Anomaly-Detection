"""
test_completion_rate.py — AMR Fleet Analytics, Phase 2B

Quick, standalone check of the compute_completion_rate design against
real data, before deciding where it lives permanently.

completed = status == "completed"
attributable_negative = status == "cancelled" AND total_error_events > 0
  (a logged error is real evidence of a robot-side problem)
Everything else (error-free cancellations, pending) is excluded as
insufficient evidence to attribute fault either way.
"""

import pandas as pd


def compute_completion_rate(df):
    completed = (df["status"] == "completed").sum()
    attributable_negative = ((df["status"] == "cancelled") & (df["total_error_events"] > 0)).sum()
    total = completed + attributable_negative
    if total == 0:
        return None  # insufficient evidence either way
    return completed / total


df = pd.read_csv("ml_features_v3.csv")

for robot in sorted(df["robot_name"].unique()):
    subset = df[df["robot_name"] == robot]
    rate = compute_completion_rate(subset)
    n_completed = (subset["status"] == "completed").sum()
    n_attributable = ((subset["status"] == "cancelled") & (subset["total_error_events"] > 0)).sum()
    n_total = len(subset)
    if rate is None:
        print(f"{robot}: completion_rate = None (insufficient evidence — "
              f"{n_completed} completed, {n_attributable} attributable-negative, "
              f"out of {n_total} total missions)")
    else:
        print(f"{robot}: completion_rate = {rate:.4f} "
              f"({n_completed} completed / {n_completed + n_attributable} evaluable, "
              f"out of {n_total} total missions)")