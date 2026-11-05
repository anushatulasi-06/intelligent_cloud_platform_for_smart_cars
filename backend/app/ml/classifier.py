"""CNN classifier (CPU only). The network mirrors model/cnn.py (AudioCNN) layer for layer, with the
same parameter names, so weights saved by training load with strict=True. Sizes (channels, pooled
grid, dropout, number of classes) come from config.json.

    3 x [Conv3x3 (no bias) -> BatchNorm -> ReLU -> MaxPool2x2]
    AdaptiveAvgPool2d(pool_size) -> Flatten -> Dropout -> Linear(channels[-1]*pool_h*pool_w -> classes)
"""
import json
from pathlib import Path
from typing import Any

import torch
from torch import nn

from app.ml.model_config import ModelConfigError, load_model_config

EXPECTED_LABELS = ["emergency", "in_vehicle", "human", "traffic", "environmental"]
WEIGHTS_FILE, LABELS_FILE = "model_weights.pt", "labels.json"


class ModelLoadError(RuntimeError):
    """Artifacts missing or inconsistent with each other."""


def _conv_block(in_ch: int, out_ch: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1, bias=False),
        nn.BatchNorm2d(out_ch),
        nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
    )


class AudioCNN(nn.Module):
    def __init__(self, architecture: dict[str, Any]):
        super().__init__()
        channels = list(architecture["channels"])
        pool_h, pool_w = architecture["pool_size"]
        blocks, in_ch = [], 1
        for out_ch in channels:
            blocks.append(_conv_block(in_ch, out_ch))
            in_ch = out_ch
        self.features = nn.Sequential(*blocks)
        self.pool = nn.AdaptiveAvgPool2d((pool_h, pool_w))
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(architecture["dropout"]),
            nn.Linear(channels[-1] * pool_h * pool_w, architecture["num_classes"]),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.pool(self.features(x)))


def read_labels(path: Path) -> list[str]:
    raw = json.loads(path.read_text())
    return [raw[str(i)] for i in range(len(raw))]


class CnnClassifier:
    def __init__(self, network: AudioCNN, labels: list[str], config: dict[str, Any]):
        self.network = network.eval()
        self.labels = labels
        self.config = config
        self.input_shape = tuple(config["input_shape"])

    @classmethod
    def load(cls, model_dir: str | Path) -> "CnnClassifier":
        """Read config.json, labels.json and model_weights.pt from one version folder."""
        model_dir = Path(model_dir)
        try:
            config = load_model_config(model_dir)
        except ModelConfigError as exc:
            raise ModelLoadError(str(exc)) from exc
        for name in (WEIGHTS_FILE, LABELS_FILE):
            if not (model_dir / name).is_file():
                raise ModelLoadError(f"{model_dir / name} not found")
        labels = read_labels(model_dir / LABELS_FILE)
        if labels != EXPECTED_LABELS or labels != list(config["categories"]):
            raise ModelLoadError(f"labels.json {labels} must be {EXPECTED_LABELS} and match config.json")
        network = AudioCNN(config["architecture"])
        try:
            state = torch.load(model_dir / WEIGHTS_FILE, map_location="cpu", weights_only=True)
            network.load_state_dict(state, strict=True)
        except Exception as exc:
            raise ModelLoadError(f"could not load {model_dir / WEIGHTS_FILE}: {exc}") from exc
        return cls(network, labels, config)

    @torch.inference_mode()
    def predict(self, spec: torch.Tensor) -> dict[str, float]:
        """spec [1, n_mels, n_frames] (or [B, 1, ...] with B=1) -> {category: probability}, sums to 1."""
        x = spec.unsqueeze(0) if spec.dim() == 3 else spec
        if tuple(x.shape[1:]) != self.input_shape:
            raise ValueError(f"input shape {tuple(x.shape[1:])} != trained shape {self.input_shape}")
        probs = torch.softmax(self.network(x.float()), dim=1)[0]
        return {label: float(p) for label, p in zip(self.labels, probs)}
