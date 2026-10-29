"""Training loop.

One job: train the AudioCNN on the manifest's TRAIN split, pick the best epoch
using the VAL split, and save the result to model/checkpoints/.

Run end-to-end with one command (any of these work):
    cd model && python train.py
    python model/train.py                 # from the repo root
    python -m model.train                 # from the repo root
Prerequisite: `python -m model.dataset_prep` has produced model/data/manifest.json.

Outputs (model/checkpoints/)
    cnn_weights.pth        best model's state_dict (load with model.load_state_dict)
    labels.json            {"0": "emergency", "1": "in_vehicle", ...}  index -> category
    training_summary.json  hyper-parameters, per-epoch history, best epoch, and the
                           audio/preprocessing settings used (for sanity checks at load time)

Choices worth knowing
    * Loss is class-weighted cross-entropy (inverse frequency) because the classes
      are very imbalanced (human ~45% of train, traffic ~7%). Disable with
      --no-class-weights.
    * "Best" epoch = highest VAL BALANCED accuracy (mean per-class recall), not raw
      accuracy: with this imbalance, raw accuracy rewards predicting the big
      classes. Both numbers are logged every epoch; ties broken by lower val loss.
    * Early stopping if the val score does not improve for --patience epochs.
    * The TEST split is never touched here -- that is evaluate.py's job.
    * No augmentation yet (add it in dataset.py for the train split only).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

try:  # works as `model.train` and as a plain script (cd model && python train.py)
    from . import config
    from .cnn import AudioCNN, count_parameters
    from .dataset import AudioDataset
except ImportError:  # pragma: no cover
    import config  # type: ignore
    from cnn import AudioCNN, count_parameters  # type: ignore
    from dataset import AudioDataset  # type: ignore


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def atomic_save_weights(state_dict: dict, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save(state_dict, tmp)
    os.replace(tmp, path)


@torch.no_grad()
def evaluate(model: nn.Module, loader: DataLoader, loss_fn: nn.Module, device: torch.device) -> dict:
    """Loss, accuracy, balanced accuracy and per-class recall on a loader."""
    model.eval()
    n_cls = len(config.CATEGORIES)
    total_loss, n = 0.0, 0
    hits = torch.zeros(n_cls)
    seen = torch.zeros(n_cls)
    for x, y in loader:
        x, y = x.to(device), y.to(device)
        logits = model(x)
        total_loss += loss_fn(logits, y).item() * len(y)
        n += len(y)
        pred = logits.argmax(1)
        for c in range(n_cls):
            mask = y == c
            seen[c] += mask.sum().item()
            hits[c] += (pred[mask] == c).sum().item()
    recall = torch.where(seen > 0, hits / seen.clamp(min=1), torch.zeros(n_cls))
    present = seen > 0
    return {
        "loss": total_loss / max(n, 1),
        "acc": hits.sum().item() / max(n, 1),
        "balanced_acc": recall[present].mean().item() if present.any() else 0.0,
        "per_class_recall": {c: round(recall[i].item(), 4) for i, c in enumerate(config.CATEGORIES)},
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Train the audio CNN.")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--patience", type=int, default=10, help="early-stop after N epochs without improvement")
    ap.add_argument("--seed", type=int, default=config.SPLIT_SEED)
    ap.add_argument("--no-class-weights", action="store_true", help="plain (unweighted) cross-entropy")
    args = ap.parse_args()

    set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    config.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- data (train + val only) ----
    train_ds, val_ds = AudioDataset("train"), AudioDataset("val")
    g = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, generator=g)
    val_loader = DataLoader(val_ds, batch_size=64, shuffle=False)
    print(f"device={device}  train={len(train_ds)}  val={len(val_ds)}")
    print("train class counts:", dict(zip(config.CATEGORIES, train_ds.class_counts())))
    print("val   class counts:", dict(zip(config.CATEGORIES, val_ds.class_counts())))

    # ---- model / loss / optimizer ----
    model = AudioCNN().to(device)
    weights = None if args.no_class_weights else train_ds.class_weights().to(device)
    train_loss_fn = nn.CrossEntropyLoss(weight=weights)
    eval_loss_fn = nn.CrossEntropyLoss()      # unweighted, so val loss is comparable across runs
    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    print(f"model: AudioCNN, {count_parameters(model):,} parameters; "
          f"class-weighted loss: {weights is not None}\n")

    # ---- labels.json (index -> category) written up front ----
    atomic_write_text(config.LABELS_PATH,
                      json.dumps({str(i): c for i, c in enumerate(config.CATEGORIES)}, indent=2))

    # ---- training loop ----
    history: list[dict] = []
    best = {"epoch": 0, "balanced_acc": -1.0, "val_loss": float("inf")}
    stale = 0
    print(f"{'epoch':>5} {'train_loss':>10} {'train_acc':>9} {'val_loss':>9} {'val_acc':>8} "
          f"{'val_bal_acc':>11} {'sec':>5}")
    t_start = time.time()
    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        model.train()
        run_loss, run_hits, n = 0.0, 0, 0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            logits = model(x)
            loss = train_loss_fn(logits, y)
            loss.backward()
            opt.step()
            run_loss += loss.item() * len(y)
            run_hits += (logits.argmax(1) == y).sum().item()
            n += len(y)
        val = evaluate(model, val_loader, eval_loss_fn, device)

        row = {"epoch": epoch, "train_loss": run_loss / n, "train_acc": run_hits / n,
               "val_loss": val["loss"], "val_acc": val["acc"], "val_balanced_acc": val["balanced_acc"]}
        history.append(row)

        improved = (val["balanced_acc"] > best["balanced_acc"] + 1e-6 or
                    (abs(val["balanced_acc"] - best["balanced_acc"]) <= 1e-6 and val["loss"] < best["val_loss"]))
        if improved:
            best = {"epoch": epoch, "balanced_acc": val["balanced_acc"], "val_acc": val["acc"],
                    "val_loss": val["loss"], "val_per_class_recall": val["per_class_recall"]}
            atomic_save_weights(model.state_dict(), config.WEIGHTS_PATH)   # keep the best on disk
            stale = 0
        else:
            stale += 1
        print(f"{epoch:>5} {row['train_loss']:>10.4f} {row['train_acc']:>9.3f} {row['val_loss']:>9.4f} "
              f"{row['val_acc']:>8.3f} {row['val_balanced_acc']:>11.3f} {time.time() - t0:>5.1f}"
              f"{'  * saved' if improved else ''}")
        if stale >= args.patience:
            print(f"\nearly stopping: no val improvement for {args.patience} epochs")
            break

    # ---- summary ----
    summary = {
        "best": best,
        "stopped_epoch": len(history),
        "train_seconds": round(time.time() - t_start, 1),
        "hyperparameters": {k: v for k, v in vars(args).items()},
        "class_weighted_loss": weights is not None,
        "selection_metric": "val balanced accuracy (mean per-class recall)",
        "preprocessing": {
            "sample_rate": config.SAMPLE_RATE, "clip_duration_s": config.CLIP_DURATION_S,
            "n_mels": config.N_MELS, "n_fft": config.N_FFT, "hop_length": config.HOP_LENGTH,
            "n_frames": config.N_FRAMES, "top_db": config.TOP_DB,
        },
        "categories": config.CATEGORIES,
        "history": history,
    }
    atomic_write_text(config.TRAIN_SUMMARY_PATH, json.dumps(summary, indent=2))

    print(f"\nbest epoch {best['epoch']}: val balanced acc {best['balanced_acc']:.3f}, "
          f"val acc {best['val_acc']:.3f}, val loss {best['val_loss']:.4f}")
    print("val per-class recall at best epoch:")
    for c, r in best["val_per_class_recall"].items():
        print(f"  {c:<14}{r:.3f}")
    print(f"\nsaved: {config.WEIGHTS_PATH}\n       {config.LABELS_PATH}\n       {config.TRAIN_SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
