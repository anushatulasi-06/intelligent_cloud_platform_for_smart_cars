"""Package the TRAINED checkpoint (model/checkpoints) as a backend model version:
model/artifacts/<version>/{model_weights.pt, labels.json, config.json}.

    python scripts/export_model.py --version cnn-v1

Checks first that the checkpoint was trained with the preprocessing now in model/config.py
(training_summary.json) and that labels.json matches the category order.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_artifacts import DEFAULT_ARTIFACTS_DIR, REPO_ROOT, build_config, write_version  # noqa: E402

CHECKPOINTS = REPO_ROOT / "model" / "checkpoints"


def export(version: str, out: Path = DEFAULT_ARTIFACTS_DIR, checkpoints: Path = CHECKPOINTS) -> Path:
    import torch

    config = build_config(version, dummy=False)
    summary_path = checkpoints / "training_summary.json"
    if summary_path.is_file():
        trained = json.loads(summary_path.read_text()).get("preprocessing", {})
        now = {"sample_rate": config["audio"]["sample_rate"], "clip_duration_s": config["audio"]["duration_s"],
               "n_mels": config["features"]["n_mels"], "n_fft": config["features"]["n_fft"],
               "hop_length": config["features"]["hop_length"], "n_frames": config["features"]["n_frames"],
               "top_db": config["features"]["top_db"]}
        diff = {k: (trained.get(k), v) for k, v in now.items() if trained.get(k) != v}
        if diff:
            raise SystemExit(f"model/config.py differs from the training run (trained, now): {diff}")
    labels = json.loads((checkpoints / "labels.json").read_text())
    if [labels[str(i)] for i in range(len(labels))] != config["categories"]:
        raise SystemExit("checkpoints/labels.json does not match model/config.py CATEGORIES")
    state = torch.load(checkpoints / "cnn_weights.pth", map_location="cpu", weights_only=True)
    return write_version(Path(out) / version, config, state)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    args = ap.parse_args()
    print(f"Exported trained model to {export(args.version, args.out)}")


if __name__ == "__main__":
    main()
