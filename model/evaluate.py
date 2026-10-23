"""Model evaluation on the held-out TEST split.

One job: measure how well the trained checkpoint really performs, and derive a
data-driven CONFIDENCE_THRESHOLD for the anomaly check. Does not modify weights
or config.py -- it only prints a recommendation (and writes a JSON report).

Run (any of):
    cd model && python evaluate.py
    python -m model.evaluate                 # from the repo root

Sections
    1. Overall accuracy, confusion matrix (rows = true, cols = predicted),
       per-class precision / recall / F1.
    2. Confidence calibration: the confidence given to every WRONG prediction,
       accuracy per confidence bucket, expected calibration error (ECE).
    3. CPU latency per clip, preprocessing (audio_utils.preprocess) and CNN
       forward pass timed separately, averaged over many runs.
    4. CONFIDENCE_THRESHOLD recommendation.

How the threshold is chosen
    The anomaly check flags a prediction when top-class confidence < threshold.
    A good threshold flags the predictions that are likely wrong without flagging
    too many correct ones. For each candidate threshold t we compute
        caught      = share of WRONG predictions with confidence < t
        false_alarm = share of CORRECT predictions with confidence < t
    and choose the t that maximizes (caught - false_alarm) (Youden's J), over a
    0.05 grid. It is tuned on the VAL split, NOT on test, and then reported on
    test as an honest check. Caveats are printed with the result (few errors =>
    noisy estimate; val was also used to pick the checkpoint epoch; and this only
    measures in-distribution mistakes -- sounds from outside the known categories are
    not covered by the held-out data).

Python/numpy/torch only (no scikit-learn).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from torch.utils.data import DataLoader

try:  # works as `model.evaluate` and as a plain script
    from . import audio_utils, config
    from .cnn import AudioCNN
    from .dataset import AudioDataset
except ImportError:  # pragma: no cover
    import audio_utils  # type: ignore
    import config  # type: ignore
    from cnn import AudioCNN  # type: ignore
    from dataset import AudioDataset  # type: ignore

CATS = config.CATEGORIES
N_CLS = len(CATS)
BUCKET_EDGES = [0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0000001]
THRESHOLD_GRID = [round(t, 2) for t in np.arange(0.30, 0.9501, 0.05)]
MIN_ERRORS_FOR_TUNING = 5


# --------------------------------------------------------------------------- #
# loading + inference
# --------------------------------------------------------------------------- #
def load_model() -> AudioCNN:
    if not config.WEIGHTS_PATH.is_file():
        raise SystemExit(f"error: {config.WEIGHTS_PATH} not found -- run train.py first")

    # The checkpoint must have been trained with the preprocessing we use now.
    if config.TRAIN_SUMMARY_PATH.is_file():
        pre = json.loads(config.TRAIN_SUMMARY_PATH.read_text()).get("preprocessing", {})
        now = {"sample_rate": config.SAMPLE_RATE, "clip_duration_s": config.CLIP_DURATION_S,
               "n_mels": config.N_MELS, "n_fft": config.N_FFT, "hop_length": config.HOP_LENGTH,
               "n_frames": config.N_FRAMES, "top_db": config.TOP_DB}
        diff = {k: (pre.get(k), v) for k, v in now.items() if pre.get(k) != v}
        if diff:
            raise SystemExit(f"error: config.py differs from the settings the model was trained with "
                             f"(trained, now): {diff}. Retrain or restore config.py.")
    if config.LABELS_PATH.is_file():
        labels = json.loads(config.LABELS_PATH.read_text())
        if [labels[str(i)] for i in range(len(labels))] != CATS:
            raise SystemExit("error: labels.json does not match config.CATEGORIES")

    model = AudioCNN()
    model.load_state_dict(torch.load(config.WEIGHTS_PATH, map_location="cpu", weights_only=True))
    return model.eval()


@torch.no_grad()
def predict_split(model: AudioCNN, split: str) -> dict:
    """Run the model over a split. Returns numpy arrays: y_true, y_pred, conf, probs, paths."""
    ds = AudioDataset(split, cache=False)
    probs, ys = [], []
    for x, y in DataLoader(ds, batch_size=64, shuffle=False):
        probs.append(torch.softmax(model(x), dim=1))
        ys.append(y)
    probs = torch.cat(probs).numpy()
    y_true = torch.cat(ys).numpy()
    return {"y_true": y_true, "y_pred": probs.argmax(1), "conf": probs.max(1), "probs": probs,
            "paths": [p for p, _ in ds.samples]}


# --------------------------------------------------------------------------- #
# 1. classification metrics
# --------------------------------------------------------------------------- #
def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    cm = np.zeros((N_CLS, N_CLS), dtype=int)
    np.add.at(cm, (y_true, y_pred), 1)
    return cm


def per_class_metrics(cm: np.ndarray) -> list[dict]:
    rows = []
    for i, c in enumerate(CATS):
        tp = cm[i, i]
        fp = cm[:, i].sum() - tp
        fn = cm[i, :].sum() - tp
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        rows.append({"category": c, "precision": p, "recall": r, "f1": f1, "support": int(cm[i].sum())})
    return rows


def save_confusion_image(cm: np.ndarray) -> str:
    """Write checkpoints/confusion_matrix.png. Rows are true labels, columns predicted."""
    names = CATS
    fig, ax = plt.subplots(figsize=(8, 6.5))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(names)), labels=names, rotation=35, ha="right")
    ax.set_yticks(range(len(names)), labels=names)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title("Test confusion matrix")
    peak = float(cm.max()) if cm.size else 0.0
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, str(int(cm[i, j])), ha="center", va="center",
                    color="white" if cm[i, j] > peak / 2 else "black")
    fig.colorbar(im, ax=ax, fraction=0.046)
    fig.tight_layout()
    config.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CHECKPOINT_DIR / "confusion_matrix.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return str(path)


def print_classification(res: dict) -> dict:
    y_true, y_pred = res["y_true"], res["y_pred"]
    cm = confusion_matrix(y_true, y_pred)
    rows = per_class_metrics(cm)
    acc = float((y_true == y_pred).mean())
    macro_f1 = float(np.mean([r["f1"] for r in rows]))
    bal_acc = float(np.mean([r["recall"] for r in rows if r["support"] > 0]))

    print("=" * 78)
    print("1. CLASSIFICATION  (test split, n=%d)" % len(y_true))
    print("=" * 78)
    print(f"overall accuracy : {acc:.4f}  ({int((y_true == y_pred).sum())}/{len(y_true)})")
    print(f"balanced accuracy: {bal_acc:.4f}   macro F1: {macro_f1:.4f}")

    print("\nconfusion matrix (rows = TRUE, columns = PREDICTED):")
    w = 6
    print(" " * 15 + "".join(f"{c[:5]:>{w}}" for c in CATS) + "   total")
    for i, c in enumerate(CATS):
        print(f"{c:<15}" + "".join(f"{v:>{w}}" for v in cm[i]) + f"{cm[i].sum():>8}")

    print(f"\n{'category':<15}{'precision':>10}{'recall':>9}{'f1':>8}{'support':>9}")
    for r in rows:
        print(f"{r['category']:<15}{r['precision']:>10.3f}{r['recall']:>9.3f}{r['f1']:>8.3f}{r['support']:>9}")
    print(f"{'macro avg':<15}{np.mean([r['precision'] for r in rows]):>10.3f}"
          f"{np.mean([r['recall'] for r in rows]):>9.3f}{macro_f1:>8.3f}{len(y_true):>9}")
    small = [r["category"] for r in rows if 0 < r["support"] < 20]
    if small:
        print(f"note: only {min(r['support'] for r in rows)}-19 test clips for {small}; "
              f"their per-class numbers are coarse (1 clip = several %).")
    image = save_confusion_image(cm)
    print(f"\nconfusion matrix image: {image}")
    return {"accuracy": acc, "balanced_accuracy": bal_acc, "macro_f1": macro_f1,
            "confusion_matrix": cm.tolist(), "confusion_matrix_image": image, "per_class": rows}


# --------------------------------------------------------------------------- #
# 2. calibration
# --------------------------------------------------------------------------- #
def print_calibration(res: dict) -> dict:
    y_true, y_pred, conf = res["y_true"], res["y_pred"], res["conf"]
    correct = y_true == y_pred
    wrong = ~correct

    print("\n" + "=" * 78)
    print("2. CONFIDENCE CALIBRATION")
    print("=" * 78)
    print(f"mean confidence: correct = {conf[correct].mean():.3f}"
          + (f", wrong = {conf[wrong].mean():.3f}" if wrong.any() else ""))

    wrong_list = []
    if wrong.any():
        wc = conf[wrong]
        print(f"\nWRONG predictions: {int(wrong.sum())}   confidence given: "
              f"min {wc.min():.3f}  median {np.median(wc):.3f}  mean {wc.mean():.3f}  max {wc.max():.3f}")
        print(f"  confidently wrong (conf >= 0.90): {int((wc >= 0.90).sum())}   "
              f"(conf >= 0.70): {int((wc >= 0.70).sum())}   (conf < 0.50): {int((wc < 0.50).sum())}")
        print(f"\n  {'conf':>6}  {'true':<14}{'predicted':<14}file")
        for idx in np.argsort(-conf[wrong]):
            i = np.flatnonzero(wrong)[idx]
            name = Path(res["paths"][i]).name
            print(f"  {conf[i]:>6.3f}  {CATS[y_true[i]]:<14}{CATS[y_pred[i]]:<14}{name}")
            wrong_list.append({"file": name, "true": CATS[y_true[i]], "pred": CATS[y_pred[i]],
                               "confidence": round(float(conf[i]), 4)})
    else:
        print("\nno wrong predictions on this split")

    print(f"\naccuracy by confidence bucket:")
    print(f"  {'confidence':<13}{'n':>5}{'accuracy':>10}{'mean conf':>11}{'gap':>8}")
    buckets = []
    for lo, hi in zip(BUCKET_EDGES[:-1], BUCKET_EDGES[1:]):
        m = (conf >= lo) & (conf < hi)
        label = f"[{lo:.2f},{min(hi, 1.0):.2f}{']' if hi > 1 else ')'}"
        if m.sum() == 0:
            print(f"  {label:<13}{0:>5}{'-':>10}")
            buckets.append({"range": label, "n": 0})
            continue
        a, c = float(correct[m].mean()), float(conf[m].mean())
        print(f"  {label:<13}{int(m.sum()):>5}{a:>10.3f}{c:>11.3f}{c - a:>+8.3f}")
        buckets.append({"range": label, "n": int(m.sum()), "accuracy": a, "mean_confidence": c})

    # expected calibration error, 10 equal-width bins
    edges = np.linspace(0, 1, 11)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            ece += m.mean() * abs(correct[m].mean() - conf[m].mean())
    print(f"\nexpected calibration error (ECE, 10 bins): {ece:.4f}  "
          f"(gap > 0 above = over-confident bucket)")
    return {"mean_conf_correct": float(conf[correct].mean()),
            "mean_conf_wrong": float(conf[wrong].mean()) if wrong.any() else None,
            "wrong_predictions": wrong_list, "buckets": buckets, "ece": float(ece)}


# --------------------------------------------------------------------------- #
# 3. latency
# --------------------------------------------------------------------------- #
def benchmark_latency(model: AudioCNN, paths: list[str], runs: int, threads: int) -> dict:
    torch.set_num_threads(threads)
    paths = [paths[i % len(paths)] for i in range(runs)]
    warm = paths[:3]
    with torch.inference_mode():
        for p in warm:                                   # warm-up (imports, caches, allocator)
            model(torch.from_numpy(audio_utils.preprocess(p))[None, None])
        pre_ms, fwd_ms = [], []
        for p in paths:
            t0 = time.perf_counter()
            spec = audio_utils.preprocess(p)
            t1 = time.perf_counter()
            x = torch.from_numpy(spec)[None, None]
            logits = model(x)
            probs = torch.softmax(logits, dim=1)
            _ = (int(probs.argmax()), float(probs.max()))
            t2 = time.perf_counter()
            pre_ms.append((t1 - t0) * 1000)
            fwd_ms.append((t2 - t1) * 1000)

    def stats(v: list[float]) -> dict:
        s = sorted(v)
        return {"mean": statistics.fmean(v), "median": statistics.median(v),
                "p95": s[min(len(s) - 1, int(0.95 * len(s)))], "max": s[-1]}

    pre, fwd = stats(pre_ms), stats(fwd_ms)
    tot = stats([a + b for a, b in zip(pre_ms, fwd_ms)])
    print("\n" + "=" * 78)
    print(f"3. CPU LATENCY per clip  ({runs} timed runs, batch=1, {threads} torch thread(s), "
          f"after 3 warm-up runs)")
    print("=" * 78)
    print(f"  {'stage':<28}{'mean':>8}{'median':>9}{'p95':>8}{'max':>8}   (ms)")
    print(f"  {'preprocess (load+mel)':<28}{pre['mean']:>8.1f}{pre['median']:>9.1f}{pre['p95']:>8.1f}{pre['max']:>8.1f}")
    print(f"  {'CNN forward (+softmax)':<28}{fwd['mean']:>8.2f}{fwd['median']:>9.2f}{fwd['p95']:>8.2f}{fwd['max']:>8.2f}")
    print(f"  {'total':<28}{tot['mean']:>8.1f}{tot['median']:>9.1f}{tot['p95']:>8.1f}{tot['max']:>8.1f}")
    print("  note: measured on THIS machine's CPU, not a t3.micro/small -- expect a t3 to be "
          "slower; re-run there for real numbers. Disk is warm after warm-up; a model load "
          "cost (once per process) is not included.")
    return {"runs": runs, "threads": threads, "preprocess_ms": pre, "forward_ms": fwd, "total_ms": tot}


# --------------------------------------------------------------------------- #
# 4. threshold recommendation
# --------------------------------------------------------------------------- #
def sweep(conf: np.ndarray, correct: np.ndarray) -> list[dict]:
    n_wrong, n_ok = int((~correct).sum()), int(correct.sum())
    out = []
    for t in THRESHOLD_GRID:
        flagged = conf < t
        caught = float((flagged & ~correct).sum() / n_wrong) if n_wrong else float("nan")
        false_alarm = float((flagged & correct).sum() / n_ok) if n_ok else float("nan")
        accepted = ~flagged
        out.append({
            "threshold": t,
            "flagged_pct": float(flagged.mean()),
            "errors_caught": caught,
            "false_alarms": false_alarm,
            "accepted_accuracy": float(correct[accepted].mean()) if accepted.any() else float("nan"),
            "youden_j": caught - false_alarm if n_wrong else float("nan"),
        })
    return out


def print_sweep(title: str, rows: list[dict], highlight: float | None = None) -> None:
    print(f"\n  {title}")
    print(f"  {'thr':>5}{'flagged':>9}{'errors caught':>15}{'correct flagged':>17}{'accuracy kept':>15}{'J':>7}")
    for r in rows:
        mark = "  <-- recommended" if highlight is not None and abs(r["threshold"] - highlight) < 1e-9 else \
               ("  (config default)" if abs(r["threshold"] - config.CONFIDENCE_THRESHOLD) < 1e-9 else "")
        print(f"  {r['threshold']:>5.2f}{r['flagged_pct']:>9.1%}{r['errors_caught']:>15.1%}"
              f"{r['false_alarms']:>17.1%}{r['accepted_accuracy']:>15.1%}{r['youden_j']:>7.2f}{mark}")


def recommend_threshold(val: dict, test: dict) -> dict:
    v_correct = val["y_true"] == val["y_pred"]
    t_correct = test["y_true"] == test["y_pred"]
    n_val_err = int((~v_correct).sum())
    val_rows = sweep(val["conf"], v_correct)
    test_rows = sweep(test["conf"], t_correct)

    print("\n" + "=" * 78)
    print("4. CONFIDENCE_THRESHOLD RECOMMENDATION")
    print("=" * 78)
    print("anomaly flag = top-class confidence < threshold.  Tuned on VAL, checked on TEST.")
    print_sweep(f"VAL  (n={len(v_correct)}, wrong={n_val_err})", val_rows)
    print_sweep(f"TEST (n={len(t_correct)}, wrong={int((~t_correct).sum())})", test_rows)

    if n_val_err < MIN_ERRORS_FOR_TUNING:
        rec = config.CONFIDENCE_THRESHOLD
        print(f"\nRECOMMENDATION: keep CONFIDENCE_THRESHOLD = {rec:.2f}. Only {n_val_err} validation "
              f"errors -- too few to tune a threshold from data.")
        return {"recommended": rec, "reason": "too few validation errors", "val_sweep": val_rows,
                "test_sweep": test_rows}

    best_j = max(r["youden_j"] for r in val_rows)
    rec_row = next(r for r in val_rows if abs(r["youden_j"] - best_j) < 1e-9)   # lowest t among ties
    rec = rec_row["threshold"]
    t_row = next(r for r in test_rows if abs(r["threshold"] - rec) < 1e-9)
    d_row = next((r for r in test_rows if abs(r["threshold"] - config.CONFIDENCE_THRESHOLD) < 1e-9), None)

    print_sweep("(recommended row marked)", [rec_row], highlight=rec)
    print("\n" + "-" * 78)
    print(f"RECOMMENDED  CONFIDENCE_THRESHOLD = {rec:.2f}      (set it in model/config.py)")
    print("-" * 78)
    print(f"  tuned on val  : flags {rec_row['errors_caught']:.0%} of wrong predictions, "
          f"at the cost of flagging {rec_row['false_alarms']:.0%} of correct ones.")
    print(f"  checked on test: flags {t_row['errors_caught']:.0%} of wrong predictions, "
          f"flags {t_row['false_alarms']:.0%} of correct ones; accuracy of the clips it lets "
          f"through = {t_row['accepted_accuracy']:.1%} (vs {float(t_correct.mean()):.1%} with no filter); "
          f"{t_row['flagged_pct']:.1%} of all clips flagged.")
    if d_row is not None:
        print(f"  for comparison, the {config.CONFIDENCE_THRESHOLD:.2f} default on test: flags "
              f"{d_row['errors_caught']:.0%} of errors, {d_row['false_alarms']:.0%} of correct, "
              f"{d_row['flagged_pct']:.1%} of all clips.")
    print("\n  caveats:")
    print(f"   - based on only {n_val_err} val / {int((~t_correct).sum())} test errors; treat it as +/- 0.05-0.10.")
    print("   - val was also used to choose the checkpoint epoch, so val confidences are slightly flattering.")
    print("   - this measures mistakes between the known categories. Sounds from OUTSIDE them "
          "(e.g. baby crying) are not in the held-out data, so how well the threshold flags "
          "those is untested -- check with a few out-of-category clips before relying on it.")
    return {"recommended": rec, "val_row": rec_row, "test_row": t_row, "val_sweep": val_rows,
            "test_sweep": test_rows, "method": "maximize (errors caught - correct flagged) on val, 0.05 grid"}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate the trained CNN on the held-out test split.")
    ap.add_argument("--latency-runs", type=int, default=50, help="timed runs for the latency benchmark (>=20)")
    ap.add_argument("--threads", type=int, default=1, help="torch CPU threads for the latency benchmark")
    ap.add_argument("--no-report", action="store_true", help="don't write evaluation_report.json")
    args = ap.parse_args()
    if args.latency_runs < 20:
        ap.error("--latency-runs must be at least 20")

    model = load_model()
    print(f"loaded {config.WEIGHTS_PATH.name}; categories: {CATS}\n")
    test = predict_split(model, "test")
    val = predict_split(model, "val")

    report = {"classification": print_classification(test),
              "calibration": print_calibration(test),
              "latency": benchmark_latency(model, test["paths"], args.latency_runs, args.threads),
              "threshold": recommend_threshold(val, test)}

    if not args.no_report:
        config.EVAL_REPORT_PATH.write_text(json.dumps(report, indent=2, default=float))
        print(f"\nfull report written to {config.EVAL_REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
