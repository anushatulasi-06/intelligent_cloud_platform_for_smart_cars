"""Build and write a model version folder: <MODEL_DIR>/<version>/{model_weights.pt, labels.json, config.json}.

config.json is generated from the TRAINING code so inference matches it exactly:
  * audio and feature values      from model/config.py
  * layer sizes                   by inspecting model/cnn.py's AudioCNN
  * librosa settings training left at their defaults (window, center, pad_mode, fmin, fmax, htk, norm,
    amin, win_length) are read from librosa's own signatures and written out explicitly, so the
    backend never depends on a library default.
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

DEFAULT_ARTIFACTS_DIR = REPO_ROOT / "model" / "artifacts"

# Steps written as code (not constants) in model/audio_utils.to_mel_spectrogram:
POWER = 2.0                                   # melspectrogram(power=2.0)
DB_REF = "max"                                # power_to_db(ref=np.max)
SCALING = "(db + top_db) / top_db"            # -> [0, 1]


def _defaults(func, *names):
    params = inspect.signature(func).parameters
    return {n: params[n].default for n in names}


def build_config(version_label: str, *, dummy: bool, confidence_threshold: float | None = None) -> dict:
    import librosa
    import torch

    from model import config as mc
    from model.cnn import AudioCNN as TrainingCNN

    spec = _defaults(librosa.feature.melspectrogram, "window", "center", "pad_mode", "win_length")
    mel = _defaults(librosa.filters.mel, "fmin", "fmax", "htk", "norm")
    db = _defaults(librosa.power_to_db, "amin")
    net = TrainingCNN()
    channels = [block[0].out_channels for block in net.features]
    pool = net.pool.output_size
    pool = [pool, pool] if isinstance(pool, int) else list(pool)

    return {
        "format_version": 1,
        "version_label": version_label,
        "dummy": dummy,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "categories": list(mc.CATEGORIES),
        "audio": {
            "sample_rate": mc.SAMPLE_RATE,
            "channels": mc.CHANNELS,
            "duration_s": mc.CLIP_DURATION_S,
            "clip_samples": mc.CLIP_SAMPLES,
            "pad": "zeros_at_end",
            "crop": "loudest_window",
            "crop_search_hop_s": mc.CROP_SEARCH_HOP_S,
        },
        "features": {
            "type": "log_mel",
            "n_fft": mc.N_FFT,
            "hop_length": mc.HOP_LENGTH,
            "win_length": spec["win_length"],
            "n_mels": mc.N_MELS,
            "window": spec["window"],
            "center": spec["center"],
            "pad_mode": spec["pad_mode"],
            "power": POWER,
            "fmin": float(mel["fmin"]),
            "fmax": float(mel["fmax"] if mel["fmax"] is not None else mc.SAMPLE_RATE / 2),
            "htk": mel["htk"],
            "mel_norm": mel["norm"],
            "db_ref": DB_REF,
            "amin": db["amin"],
            "top_db": mc.TOP_DB,
            "scaling": SCALING,
            "n_frames": mc.N_FRAMES,
        },
        "input_shape": [1, mc.N_MELS, mc.N_FRAMES],
        "architecture": {
            "name": "AudioCNN",
            "channels": channels,
            "pool_size": pool,
            "dropout": net.classifier[1].p,
            "num_classes": net.num_classes,
        },
        "confidence_threshold": mc.CONFIDENCE_THRESHOLD if confidence_threshold is None else confidence_threshold,
        "libraries": {"librosa": librosa.__version__, "torch": torch.__version__},
    }


def write_version(out_dir: Path, config: dict, state_dict) -> Path:
    """Write weights, labels and config, then prove the backend can load them."""
    import torch

    from app.ml.classifier import CnnClassifier

    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save(state_dict, out_dir / "model_weights.pt")
    (out_dir / "labels.json").write_text(
        json.dumps({str(i): c for i, c in enumerate(config["categories"])}, indent=2) + "\n")
    (out_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    clf = CnnClassifier.load(out_dir)                         # strict load + label/config checks
    probs = clf.predict(torch.zeros(config["input_shape"]))
    assert abs(sum(probs.values()) - 1.0) < 1e-5
    return out_dir
