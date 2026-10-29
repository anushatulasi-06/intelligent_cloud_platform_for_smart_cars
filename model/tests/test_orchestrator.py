"""Smoke test for model/orchestrator.py.

Runs a real prepared clip from model/data/ through preprocess_and_classify() and
checks the result is well-formed. It does NOT check that the prediction is
*correct* (that's evaluate.py's job) -- only that the pipeline runs end to end and
returns sane types and values.

Run from the repo root:
    python -m pytest model/tests -v
Requires a trained checkpoint (python -m model.train) and prepared data
(python -m model.dataset_prep); the tests are skipped with a message if missing.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:          # so `import model` works however pytest is launched
    sys.path.insert(0, str(REPO_ROOT))

from model import config, orchestrator  # noqa: E402


def _first_real_clip() -> Path:
    """One real file listed in the manifest (first test-split item), else any wav in model/data."""
    if config.MANIFEST_PATH.is_file():
        items = json.loads(config.MANIFEST_PATH.read_text())["items"]
        for it in items:
            if it["split"] == "test" and (config.DATA_DIR / it["filepath"]).is_file():
                return config.DATA_DIR / it["filepath"]
    found = sorted(config.DATA_DIR.glob("*/*.wav"))
    if found:
        return found[0]
    pytest.skip("no audio in model/data/ -- run `python -m model.dataset_prep` first")


@pytest.fixture(scope="module")
def clip() -> Path:
    if not config.WEIGHTS_PATH.is_file():
        pytest.skip("no trained checkpoint -- run `python -m model.train` first")
    return _first_real_clip()


def test_returns_valid_result(clip):
    category, confidence, is_anomaly, probs = orchestrator.preprocess_and_classify(clip)

    # valid category
    assert isinstance(category, str)
    assert category in config.CATEGORIES

    # confidence in [0, 1]
    assert isinstance(confidence, float)
    assert 0.0 <= confidence <= 1.0

    # boolean anomaly flag (a real bool, not numpy.bool_), consistent with the threshold
    assert isinstance(is_anomaly, bool)
    assert is_anomaly == (confidence < config.CONFIDENCE_THRESHOLD)

    # full probability dict: every category, valid probabilities that sum to 1
    assert set(probs) == set(config.CATEGORIES)
    assert all(0.0 <= p <= 1.0 for p in probs.values())
    assert math.isclose(sum(probs.values()), 1.0, abs_tol=1e-4)

    # the reported category/confidence are the top-1 of that dict
    assert category == max(probs, key=probs.get)
    assert math.isclose(confidence, probs[category], abs_tol=1e-6)


def test_model_is_loaded_once(clip):
    orchestrator.preprocess_and_classify(clip)
    first = orchestrator.load_model()
    orchestrator.preprocess_and_classify(clip)
    assert orchestrator.load_model() is first        # same cached object, not reloaded per call


def test_same_input_same_output(clip):
    assert orchestrator.preprocess_and_classify(clip) == orchestrator.preprocess_and_classify(clip)


def test_missing_file_raises_clear_error(clip, tmp_path):
    with pytest.raises(orchestrator.AudioLoadError):
        orchestrator.preprocess_and_classify(tmp_path / "does_not_exist.wav")
