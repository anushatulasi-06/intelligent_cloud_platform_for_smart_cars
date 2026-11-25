"""Current demo assets through the real exported CPU model, without stores or training data.

Five hand-selected examples are regression fixtures, not an accuracy benchmark.
"""
import hashlib
import json
from pathlib import Path

import pytest

from app.ml.classifier import CnnClassifier
from app.ml.preprocessor import AudioPreprocessor


ROOT = Path(__file__).resolve().parents[2]
CLIPS = ROOT / "backend" / "edge" / "demo_clips"


@pytest.fixture(scope="module")
def scenario_model():
    model = CnnClassifier.load(ROOT / "model" / "artifacts" / "cnn-v1")
    return model, AudioPreprocessor(model.config)


@pytest.mark.parametrize("category", ["emergency", "in_vehicle", "human", "traffic", "environmental"])
def test_current_scenario_clip_matches_manifest_and_expected_prediction(scenario_model, category):
    manifest = json.loads((CLIPS / "manifest.json").read_text())
    clip = next(row for row in manifest["clips"] if row["scenario"] == category)
    path = CLIPS / clip["filename"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == clip["sha256"]
    model, preprocessor = scenario_model
    scores = model.predict(preprocessor.to_log_mel(preprocessor.load(path)))
    assert max(scores, key=scores.get) == category, scores
    assert sum(scores.values()) == pytest.approx(1.0, abs=1e-5)
