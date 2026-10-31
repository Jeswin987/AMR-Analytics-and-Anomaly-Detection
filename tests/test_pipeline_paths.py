# tests/test_pipeline_paths.py
"""
The scores file is written by batch_score.py and read by explain_anomaly.py.
Both must resolve it inside anomaly_pipeline/, where it actually lives.

The pipeline scripts use bare imports (`from feature_extractor import ...`),
so anomaly_pipeline/ goes on sys.path before importing them. Importing is
safe: each script's work is behind `if __name__ == "__main__"`.
"""
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "anomaly_pipeline"
EXPECTED_SCORES_FILE = PIPELINE_DIR / "anomaly_scores_294.csv"


def test_batch_score_writes_scores_into_anomaly_pipeline(monkeypatch):
    monkeypatch.syspath_prepend(str(PIPELINE_DIR))
    import batch_score

    assert batch_score.OUTPUT_FILE == EXPECTED_SCORES_FILE


def test_explain_anomaly_reads_scores_from_anomaly_pipeline(monkeypatch):
    monkeypatch.syspath_prepend(str(PIPELINE_DIR))
    import explain_anomaly

    assert explain_anomaly.SCORES_FILE == EXPECTED_SCORES_FILE
