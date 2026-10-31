"""
batch_score.py — AMR Fleet Analytics, Phase 2A

Loads the trained Isolation Forest (from train_model.py) and scores all
294 XCR missions via anomaly_scorer.py: an IF-based score plus a
rule-based error score, combined into one final label per mission.
Writes anomaly_scores_294.csv.

Data loading and feature prep are shared with train_model.py via
feature_extractor.py. Scoring logic lives in anomaly_scorer.py and
rule_based_detector.py. All four .py files must be in the same folder
as this script.

Run from the project root, after train_model.py has produced
backend/ml/model.pkl, scaler.pkl, and model_metadata.json.
"""

import json
import sys
from pathlib import Path

import joblib

from feature_extractor import load_and_prepare
from anomaly_scorer import score_batch
from pipeline_paths import SCORES_FILE

FEATURES_FILE = Path(__file__).resolve().parent.parent / "ml_features_v3.csv"
MODEL_DIR = Path(__file__).resolve().parent.parent / "backend" / "ml"
OUTPUT_FILE = SCORES_FILE


def main():
    print("=" * 70)
    print("STEP 1 — Load the trained model, scaler, and metadata")
    print("=" * 70)
    model = joblib.load(str(MODEL_DIR / "model.pkl"))
    scaler = joblib.load(str(MODEL_DIR / "scaler.pkl"))
    with open(MODEL_DIR / "model_metadata.json") as f:
        metadata = json.load(f)
    print(f"  Model trained {metadata['trained_at_utc']}, "
          f"{metadata['training_rows']} training rows, contamination={metadata['contamination']}")
    print(f"  IF features: {metadata['if_features']}")
    print(f"  IF thresholds: {metadata['if_thresholds']}")

    print()
    print("=" * 70)
    print("STEP 2 — Load and prepare data (feature_extractor.load_and_prepare)")
    print("=" * 70)
    xcr = load_and_prepare(FEATURES_FILE, metadata["feature_caps"])

    print()
    print("=" * 70)
    print("STEP 3 — Score with the hybrid model (anomaly_scorer.score_batch)")
    print("=" * 70)
    scored = score_batch(xcr, model, scaler, metadata)

    print()
    print("=" * 70)
    print("STEP 4 — Save anomaly_scores_294.csv")
    print("=" * 70)
    scored["model_version"] = metadata["model_version"]
    output_cols = [
        "mission_id", "mission_name", "status",
        "if_score", "if_label", "error_label", "final_label",
        "physical_obstruction_score", "connectivity_failure_score", "has_escalation",
        "notes", "model_version",
    ]
    scored[output_cols].to_csv(OUTPUT_FILE, index=False)
    print(f"  Wrote {len(scored)} rows to {OUTPUT_FILE}")
    print()
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nSTOPPED — {exc}")
        sys.exit(1)