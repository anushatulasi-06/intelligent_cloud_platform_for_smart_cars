"""Inference-time wrapper -- the only module the backend should import.

One job: audio file in, classification out.

    from model.orchestrator import preprocess_and_classify

    category, confidence, is_anomaly, probs = preprocess_and_classify("/path/clip.wav")
    # category   : str   one of config.CATEGORIES (the top-1 class, even if anomalous)
    # confidence : float top-1 softmax probability, 0..1
    # is_anomaly : bool  True when confidence < config.CONFIDENCE_THRESHOLD
    # probs      : dict  {category: probability} for all categories (sums to 1)

Behaviour
    * The CNN weights and labels.json are loaded ONCE per process and cached at
      module level; every later call reuses the same model. Loading is lazy (first
      call) so importing this module is cheap and never fails; call `load_model()`
      yourself at application startup to fail fast and avoid a slow first request.
    * Preprocessing is audio_utils.preprocess() -- the exact function training used.
    * The anomaly check is only a threshold on the CNN's own confidence (no second
      model). The threshold lives in config.py (value recommended by evaluate.py).
    * Thread-safe for concurrent inference (read-only model, loaded under a lock).
    * CPU only, no gradients.

Errors
    audio_utils.AudioLoadError (re-exported here) -- the clip is missing/corrupt/empty.
    ModelLoadError             -- checkpoint missing, or inconsistent with config.py.

Deliberately NOT here: FastAPI, databases, S3, alert creation, subscriptions --
the backend service that calls this owns all of that.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import torch

try:  # works as `model.orchestrator` and as a top-level module
    from . import audio_utils, config
    from .audio_utils import AudioLoadError
    from .cnn import AudioCNN
except ImportError:  # pragma: no cover
    import audio_utils  # type: ignore
    import config  # type: ignore
    from audio_utils import AudioLoadError  # type: ignore
    from cnn import AudioCNN  # type: ignore

__all__ = ["preprocess_and_classify", "load_model", "ModelLoadError", "AudioLoadError"]


class ModelLoadError(RuntimeError):
    """The trained model can't be loaded or doesn't match config.py."""


# --------------------------------------------------------------------------- #
# module-level cache: loaded once per process
# --------------------------------------------------------------------------- #
_model: AudioCNN | None = None
_labels: list[str] | None = None            # index -> category name
_lock = threading.Lock()


def _build() -> tuple[AudioCNN, list[str]]:
    """Read checkpoint + labels.json from disk and validate them against config.py."""
    for p in (config.WEIGHTS_PATH, config.LABELS_PATH):
        if not Path(p).is_file():
            raise ModelLoadError(f"{p} not found -- train the model first (python -m model.train)")

    raw = json.loads(Path(config.LABELS_PATH).read_text())
    labels = [raw[str(i)] for i in range(len(raw))]
    if labels != config.CATEGORIES:
        raise ModelLoadError(f"labels.json {labels} does not match config.CATEGORIES {config.CATEGORIES}")

    # The checkpoint must have been trained with the preprocessing settings we use now.
    if Path(config.TRAIN_SUMMARY_PATH).is_file():
        trained = json.loads(Path(config.TRAIN_SUMMARY_PATH).read_text()).get("preprocessing", {})
        now = {"sample_rate": config.SAMPLE_RATE, "clip_duration_s": config.CLIP_DURATION_S,
               "n_mels": config.N_MELS, "n_fft": config.N_FFT, "hop_length": config.HOP_LENGTH,
               "n_frames": config.N_FRAMES, "top_db": config.TOP_DB}
        diff = {k: {"trained": trained.get(k), "now": v} for k, v in now.items() if trained.get(k) != v}
        if diff:
            raise ModelLoadError(f"config.py differs from the settings the model was trained with: {diff}")

    model = AudioCNN(num_classes=len(labels))
    try:
        state = torch.load(config.WEIGHTS_PATH, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
    except Exception as e:
        raise ModelLoadError(f"could not load {config.WEIGHTS_PATH}: {e}") from e
    return model.eval(), labels


def load_model() -> AudioCNN:
    """Load the model + labels if not already loaded (idempotent, thread-safe).
    Call once at backend startup to fail fast."""
    global _model, _labels
    if _model is None:
        with _lock:
            if _model is None:                      # double-checked: another thread may have loaded it
                _model, _labels = _build()
    return _model


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def preprocess_and_classify(audio_path: str | Path) -> tuple[str, float, bool, dict[str, float]]:
    """Classify one audio file. Returns (category, confidence, is_anomaly, full_probs_dict)."""
    model = load_model()
    assert _labels is not None

    spec = audio_utils.preprocess(audio_path)                       # np.float32 [N_MELS, N_FRAMES]
    x = torch.from_numpy(spec).unsqueeze(0).unsqueeze(0)            # -> [1, 1, N_MELS, N_FRAMES]
    with torch.inference_mode():
        probs = torch.softmax(model(x), dim=1)[0]

    confidence, idx = probs.max(dim=0)
    confidence = float(confidence)
    category = _labels[int(idx)]
    is_anomaly = bool(confidence < config.CONFIDENCE_THRESHOLD)
    full_probs = {label: float(p) for label, p in zip(_labels, probs)}
    return category, confidence, is_anomaly, full_probs
