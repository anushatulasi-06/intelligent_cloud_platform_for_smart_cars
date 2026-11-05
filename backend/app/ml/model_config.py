"""Reads <MODEL_DIR>/<version>/config.json: the ONLY source of audio, feature and architecture
parameters in the backend. Nothing here has a default; a missing key is an error, so inference can
never silently drift from training.

config.json is written next to the weights by scripts/make_dummy_model.py and scripts/export_model.py,
which copy the values from model/config.py and model/cnn.py (the training code).
"""
import json
from pathlib import Path
from typing import Any

REQUIRED = {
    "categories": None,
    "audio": ("sample_rate", "duration_s", "clip_samples", "crop_search_hop_s"),
    "features": ("n_fft", "hop_length", "n_mels", "window", "center", "pad_mode", "power", "fmin", "fmax",
                 "htk", "mel_norm", "amin", "top_db", "n_frames"),
    "input_shape": None,
    "architecture": ("channels", "pool_size", "dropout", "num_classes"),
}


class ModelConfigError(ValueError):
    """config.json is missing, incomplete or inconsistent."""


def load_model_config(model_dir: str | Path) -> dict[str, Any]:
    path = Path(model_dir) / "config.json"
    if not path.is_file():
        raise ModelConfigError(f"{path} not found")
    cfg = json.loads(path.read_text())
    validate_model_config(cfg)
    return cfg


def validate_model_config(cfg: dict[str, Any]) -> None:
    for section, keys in REQUIRED.items():
        if section not in cfg:
            raise ModelConfigError(f"config.json has no '{section}'")
        for key in keys or ():
            if key not in cfg[section]:
                raise ModelConfigError(f"config.json is missing {section}.{key}")
    a, f = cfg["audio"], cfg["features"]
    if a["clip_samples"] != int(a["sample_rate"] * a["duration_s"]):
        raise ModelConfigError("audio.clip_samples != sample_rate * duration_s")
    if list(cfg["input_shape"]) != [1, f["n_mels"], f["n_frames"]]:
        raise ModelConfigError(f"input_shape {cfg['input_shape']} != [1, n_mels, n_frames]")
    if cfg["architecture"]["num_classes"] != len(cfg["categories"]):
        raise ModelConfigError("architecture.num_classes != number of categories")
