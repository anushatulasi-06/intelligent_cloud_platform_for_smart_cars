"""Shared constants for the ML pipeline -- the single source of truth.

One job: hold every value that training, evaluation and inference must agree on,
so there is no train/serve mismatch. Nothing else in `model/` should hard-code
these numbers.

Must stay free of heavy imports (no torch/librosa) so it is cheap to import
anywhere.

Still to be added by later steps: CNN hyper-parameters (if shared) and the
final CONFIDENCE_THRESHOLD value tuned from evaluate.py's calibration results.
"""
from __future__ import annotations

import math
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
MODEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODEL_DIR.parent
RAW_DATASET_DIR = REPO_ROOT / "Audio_Datasets"       # professor-provided, read-only
DATA_DIR = MODEL_DIR / "data"                         # normalized output of dataset_prep
CHECKPOINT_DIR = MODEL_DIR / "checkpoints"
MANIFEST_PATH = DATA_DIR / "manifest.json"
WEIGHTS_PATH = CHECKPOINT_DIR / "cnn_weights.pth"    # best model (state_dict only)
LABELS_PATH = CHECKPOINT_DIR / "labels.json"          # {"0": "emergency", ...}
TRAIN_SUMMARY_PATH = CHECKPOINT_DIR / "training_summary.json"   # hyper-params, history, best epoch
EVAL_REPORT_PATH = CHECKPOINT_DIR / "evaluation_report.json"    # written by evaluate.py

# --------------------------------------------------------------------------- #
# Audio
# --------------------------------------------------------------------------- #
SAMPLE_RATE = 16_000          # Hz
CHANNELS = 1                  # mono
CLIP_DURATION_S = 4.0         # fixed model input length (pad/crop in audio_utils)
CLIP_SAMPLES = int(SAMPLE_RATE * CLIP_DURATION_S)

# --------------------------------------------------------------------------- #
# Mel-spectrogram
# --------------------------------------------------------------------------- #
N_MELS = 128
N_FFT = 2048
HOP_LENGTH = 512
N_FRAMES = 1 + CLIP_SAMPLES // HOP_LENGTH   # librosa center=True framing
TOP_DB = 80.0                 # dynamic range kept below the clip's loudest bin (dB)
CROP_SEARCH_HOP_S = 0.25      # granularity when picking the loudest window of a long clip

# --------------------------------------------------------------------------- #
# Categories (order defines the label index)
# --------------------------------------------------------------------------- #
CATEGORIES = [
    "emergency",       # sirens (ambulance)
    "in_vehicle",      # in-vehicle / accident: car crashes + glass breaking
    "human",           # screams
    "traffic",         # road traffic
    "environmental",   # wind, rain, thunderstorm, birds, doors, ...
]
# NOTE: Audio_Datasets/Alert_sounds is intentionally NOT a category (excluded
# from training -- see EXCLUDED_SOURCES).

# --------------------------------------------------------------------------- #
# Raw folder -> category mapping (used by dataset_prep.py)
#   folder: path relative to RAW_DATASET_DIR
#   category: one of CATEGORIES
#   name_regex: if set, only files whose STEM fully matches it are used;
#               the rest are reported as "excluded by rule" (not as errors).
#   relabel:    optional [(regex, category)]; a file whose stem (augmentation suffix
#               removed) fully matches regex gets that category instead of `category`.
# --------------------------------------------------------------------------- #
SOURCE_FOLDERS = [
    {"folder": "Emergency_sirens",       "category": "emergency",
     # The folder also holds ~233 ESC-50 clips of unrelated sounds (cow, frog,
     # handsaw, bells, ...). Only the ambulance recordings are real sirens.
     "name_regex": r"ambulance\d+"},
    {"folder": "car_crash_dataset",      "category": "in_vehicle",    "name_regex": None},
    {"folder": "glass_breaking_dataset", "category": "in_vehicle",    "name_regex": None},
    {"folder": "Human_Scream",           "category": "human",         "name_regex": None,
     # 28 clips named like the crash dataset ("0-0", "12-0", "31-5", ...) sit in this folder;
     # two are augmented copies of car_crash_dataset/12-0 and 14-0. They are crashes, not screams.
     "relabel": [(r"\d+-\d+", "in_vehicle")]},
    {"folder": "road_traffic_dataset",   "category": "traffic",       "name_regex": None},
    {"folder": "Environmental_Sounds",   "category": "environmental", "name_regex": None},
]

# Present on disk but deliberately not used for training.
EXCLUDED_SOURCES = [
    {"folder": "Alert_sounds/Dataset",
     "reason": "Alert_sounds excluded from training (decision: not its own category, not folded in)"},
]

# --------------------------------------------------------------------------- #
# Dataset split
# --------------------------------------------------------------------------- #
SPLIT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}
SPLIT_SEED = 42

# --------------------------------------------------------------------------- #
# Anomaly check
# --------------------------------------------------------------------------- #
# A prediction whose top-class confidence is BELOW this value is flagged as an anomaly.
# 0.75 = value recommended by `python -m model.evaluate` for the CURRENT checkpoint (5 categories,
# leakage-free group-wise split): tuned on val, checked on test it flags ~68% of wrong
# predictions and ~11% of correct ones (20% of all clips). Re-run evaluate.py and update this
# whenever the model is retrained or the split/categories change. (History: 0.85 and 0.65 were
# recommended for earlier models -- a leaky split, then the 6-category model.)
CONFIDENCE_THRESHOLD = 0.75

assert math.isclose(sum(SPLIT_RATIOS.values()), 1.0)
assert all(s["category"] in CATEGORIES for s in SOURCE_FOLDERS)
assert all(c in CATEGORIES for s in SOURCE_FOLDERS for _, c in s.get("relabel", []))
