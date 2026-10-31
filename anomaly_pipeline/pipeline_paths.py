"""
pipeline_paths.py — AMR Fleet Analytics, Phase 2A

Shared file locations for the anomaly pipeline scripts, so the writer
(batch_score.py) and the reader (explain_anomaly.py) can't disagree.
"""

from pathlib import Path

SCORES_FILE = Path(__file__).resolve().parent / "anomaly_scores_294.csv"
