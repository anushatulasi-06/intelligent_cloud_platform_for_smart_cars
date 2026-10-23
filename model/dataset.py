"""PyTorch Dataset over the train/val/test manifest.

One job: read model/data/manifest.json (written by dataset_prep.py) and yield
(spectrogram_tensor, label_index) pairs for one split.

    ds = AudioDataset("train")
    x, y = ds[0]     # x: float32 tensor [1, N_MELS, N_FRAMES], y: int (index into config.CATEGORIES)

Rules
    * Every spectrogram comes from audio_utils.preprocess() -- the exact same
      function inference uses. No preprocessing is done here.
    * Label index = position of the category in config.CATEGORIES (the same
      order the CNN's output units use).
    * The manifest is validated against config.py on load, so a stale manifest
      (different categories / sample rate / clip length) fails loudly instead of
      silently training on mismatched data.

Caching
    cache=True keeps each computed spectrogram in memory after first use
    (~65 KB per clip; ~110 MB for the whole dataset), which makes epochs after
    the first much faster. With DataLoader(num_workers>0) each worker process
    keeps its own cache unless persistent_workers=True. Turn caching off if you
    add random augmentation inside this class.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import torch
from torch.utils.data import Dataset

try:  # works as `model.dataset` and as a top-level module
    from . import audio_utils, config
except ImportError:  # pragma: no cover
    import audio_utils  # type: ignore
    import config  # type: ignore

SPLITS = tuple(config.SPLIT_RATIOS)  # ("train", "val", "test")


def load_manifest(manifest_path: str | Path = config.MANIFEST_PATH) -> dict:
    """Read the manifest and check it agrees with config.py."""
    path = Path(manifest_path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found -- run `python -m model.dataset_prep` first")
    manifest = json.loads(path.read_text())

    meta = manifest["meta"]
    problems = []
    if meta["categories"] != config.CATEGORIES:
        problems.append(f"categories {meta['categories']} != config {config.CATEGORIES}")
    if meta["sample_rate"] != config.SAMPLE_RATE:
        problems.append(f"sample_rate {meta['sample_rate']} != config {config.SAMPLE_RATE}")
    if meta["clip_duration_s"] != config.CLIP_DURATION_S:
        problems.append(f"clip_duration_s {meta['clip_duration_s']} != config {config.CLIP_DURATION_S}")
    if problems:
        raise ValueError("manifest is out of sync with model/config.py (re-run dataset_prep): "
                         + "; ".join(problems))
    return manifest


class AudioDataset(Dataset):
    def __init__(self, split: str, manifest_path: str | Path = config.MANIFEST_PATH,
                 data_dir: str | Path = config.DATA_DIR, cache: bool = True):
        if split not in SPLITS:
            raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
        self.split = split
        self.data_dir = Path(data_dir)
        self.cache = cache
        self._cache: dict[int, torch.Tensor] = {}

        manifest = load_manifest(manifest_path)
        label_of = {c: i for i, c in enumerate(config.CATEGORIES)}
        # (absolute path, label index) for this split only
        self.samples: list[tuple[Path, int]] = [
            (self.data_dir / it["filepath"], label_of[it["category"]])
            for it in manifest["items"] if it["split"] == split
        ]
        if not self.samples:
            raise ValueError(f"manifest has no items for split {split!r}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int]:
        path, label = self.samples[idx]
        x = self._cache.get(idx)
        if x is None:
            spec = audio_utils.preprocess(path)               # np.float32 [N_MELS, N_FRAMES]
            x = torch.from_numpy(spec).unsqueeze(0)           # add channel dim -> [1, N_MELS, N_FRAMES]
            if self.cache:
                self._cache[idx] = x
        return x, label

    # ---- helpers for handling class imbalance (used by train.py / evaluate.py) ----
    def class_counts(self) -> list[int]:
        """Number of samples per category, ordered like config.CATEGORIES."""
        c = Counter(label for _, label in self.samples)
        return [c.get(i, 0) for i in range(len(config.CATEGORIES))]

    def class_weights(self) -> torch.Tensor:
        """Inverse-frequency weights (mean ~1) for a weighted loss; 0 for absent classes."""
        counts = torch.tensor(self.class_counts(), dtype=torch.float32)
        present = counts > 0
        weights = torch.zeros_like(counts)
        weights[present] = counts[present].sum() / (present.sum() * counts[present])
        return weights
