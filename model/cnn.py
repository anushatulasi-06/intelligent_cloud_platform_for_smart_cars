"""CNN architecture definition ONLY.

One job: define the single classifier network. No data loading, training,
checkpoint I/O or confidence/anomaly logic lives here -- the anomaly check is a
plain threshold on this network's softmax output, applied in orchestrator.py.

Input : float tensor [batch, 1, N_MELS, N_FRAMES]   (see audio_utils.preprocess)
Output: raw logits   [batch, len(config.CATEGORIES)] (apply softmax outside)

Architecture (deliberately small -- must run on a t3.micro/small, CPU only):

    3 x [ Conv3x3 -> BatchNorm -> ReLU -> MaxPool2x2 ]      channels 1 -> 16 -> 32 -> 64
    AdaptiveAvgPool to a fixed 4x4 grid                      (coarse time/frequency layout kept)
    Flatten (64*4*4 = 1024) -> Dropout -> Linear(1024 -> num_classes)

    ~30k parameters (~120 KB of weights), ~40 M multiply-adds per clip
    (a few milliseconds on one CPU core).

Why these choices
    * 3 conv blocks: inside the 2-4 layer budget; enough depth to learn
      timbre/pattern features from a 128x126 mel image without being heavy.
    * Small channel counts (16/32/64): keeps compute and memory tiny.
    * BatchNorm: stabilizes training on the small, imbalanced dataset.
    * Adaptive pooling to 4x4 instead of global average pooling: global pooling
      would discard WHERE in frequency/time the energy is, which matters for
      sounds like sirens (sweeping tone) vs. wind (broadband noise).
      Adaptive pooling also makes the net tolerant of a different clip length.
    * Dropout before the output layer: cheap regularization (inactive in eval()).
    * Single dense output layer: exactly num_classes logits, one per category.
"""
from __future__ import annotations

import torch
from torch import nn

try:  # works as `model.cnn` and as a top-level module
    from . import config
except ImportError:  # pragma: no cover
    import config  # type: ignore


def _conv_block(in_ch: int, out_ch: int) -> nn.Sequential:
    """Conv(3x3, 'same' padding) -> BatchNorm -> ReLU -> MaxPool(2x2: halves H and W)."""
    return nn.Sequential(
        nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1, bias=False),  # BN supplies the bias
        nn.BatchNorm2d(out_ch),
        nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
    )


class AudioCNN(nn.Module):
    def __init__(self, num_classes: int | None = None, dropout: float = 0.3):
        super().__init__()
        # Output size is tied to the category list in config.py.
        self.num_classes = num_classes if num_classes is not None else len(config.CATEGORIES)

        # Feature extractor: [B,1,128,126] -> [B,16,64,63] -> [B,32,32,31] -> [B,64,16,15]
        self.features = nn.Sequential(
            _conv_block(1, 16),
            _conv_block(16, 32),
            _conv_block(32, 64),
        )
        # Fixed-size summary: [B,64,16,15] -> [B,64,4,4]
        self.pool = nn.AdaptiveAvgPool2d((4, 4))

        # Dense output layer: 1024 features -> one logit per category.
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(dropout),
            nn.Linear(64 * 4 * 4, self.num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: [B, 1, N_MELS, N_FRAMES] -> logits [B, num_classes]."""
        if x.dim() != 4 or x.shape[1] != 1:
            raise ValueError(f"expected input [B, 1, n_mels, n_frames], got {tuple(x.shape)}")
        return self.classifier(self.pool(self.features(x)))


def count_parameters(model: nn.Module) -> int:
    """Number of trainable parameters (handy for logging)."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
