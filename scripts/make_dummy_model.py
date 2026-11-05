"""Create model/artifacts/<version>/ with RANDOM weights, for running the backend without a trained
model. Predictions are meaningless; config.json marks the folder "dummy": true.

    python scripts/make_dummy_model.py                    # -> model/artifacts/cnn-dummy
    python scripts/make_dummy_model.py --version cnn-test --out /tmp/artifacts --seed 7

cnn-v1 is the real trained model (scripts/export_model.py); this script never overwrites it.

Refuses to overwrite a folder that holds real (non-dummy) weights unless --force is given.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_artifacts import DEFAULT_ARTIFACTS_DIR, build_config, write_version  # noqa: E402


def make_dummy(version: str = "cnn-dummy", out: Path = DEFAULT_ARTIFACTS_DIR, seed: int = 0,
               force: bool = False) -> Path:
    import torch

    from app.ml.classifier import AudioCNN

    target = Path(out) / version
    existing = target / "config.json"
    if existing.is_file() and not json.loads(existing.read_text()).get("dummy", False) and not force:
        raise SystemExit(f"{target} holds real weights; use --force to overwrite")
    config = build_config(version, dummy=True)
    torch.manual_seed(seed)
    network = AudioCNN(config["architecture"]).eval()
    return write_version(target, config, network.state_dict())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", default="cnn-dummy")
    ap.add_argument("--out", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    path = make_dummy(args.version, args.out, args.seed, args.force)
    print(f"Dummy model written to {path}")


if __name__ == "__main__":
    main()
