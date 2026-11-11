"""C4 unit demo: run the real ML pipeline on one audio file, without C2/C3.

    python scripts/demo_alert.py classify path/to/clip.wav
        Classify only: prints category, confidence, anomaly flag and class probabilities
        (MLOrchestrator.preprocess_and_classify with the ACTIVE model). Writes nothing.

    python scripts/demo_alert.py alert path/to/clip.wav [--car-id 1] [--device-id N]
        Full pipeline (MLOrchestrator.handle_record): stores the clip, classifies it, applies the
        anomaly threshold and the alert type's severity, upserts one alert and runs the
        notification check. The alert then appears in F12, the alerts drawer and F7 (with audio).

Run from the repo root with the backend virtualenv active, after `alembic upgrade head` and
`python -m app.db.seed`. Reads backend/.env.

Where the clip goes in `alert` mode: when LOCAL_AUDIO_DIR is set (dev), it is copied to
LOCAL_AUDIO_DIR/<s3_object_key> and read from there, so no AWS is needed; otherwise it is uploaded
to S3_BUCKET like the C3 ingest API would.
"""
import argparse
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import s3  # noqa: E402
from app.db.postgres import session_scope  # noqa: E402
from app.db.repositories import DeviceRepo  # noqa: E402
from app.services import orchestrator as orch_module  # noqa: E402
from app.services.orchestrator import get_orchestrator  # noqa: E402


def load_orchestrator():
    orch = get_orchestrator()
    model = orch.reload_model()
    if model is None:
        sys.exit("No active model in model_versions. Run `python -m app.db.seed` in backend/ first.")
    print(f"Active model: {model.version_label} (threshold {model.threshold})")
    return orch


def print_result(category: str, confidence: float, is_anomaly: bool, probs: dict) -> None:
    print(f"\nCategory   : {category}")
    print(f"Confidence : {confidence:.3f}")
    print(f"Anomaly    : {'yes (flagged for review)' if is_anomaly else 'no'}")
    print("Class probabilities:")
    for name, p in sorted(probs.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {name:<14} {p:6.3f}  {'#' * round(p * 40)}")


def cmd_classify(args) -> None:
    orch = load_orchestrator()
    print_result(*orch.preprocess_and_classify(args.audio))


def store_clip(audio: Path, key: str) -> str:
    """Put the clip where handle_record will read it. Returns a short description."""
    local_root = get_settings().LOCAL_AUDIO_DIR
    if local_root is not None:
        target = local_root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(audio, target)
        # handle_record downloads with s3.get_object; read the local copy instead (dev only).
        orch_module.s3.get_object = lambda k: (local_root / k).read_bytes()
        return f"local file {target}"
    s3.put_object(key, audio.read_bytes(), content_type="audio/wav" if audio.suffix.lower() == ".wav" else None)
    return f"s3://{get_settings().S3_BUCKET}/{key}"


def cmd_alert(args) -> None:
    device_id = args.device_id
    if device_id is None:                                   # first microphone of the car
        with session_scope() as session:
            mics = [d for d in DeviceRepo(session).list_for_car(args.car_id) if d.device_type == "microphone"]
            if not mics:
                sys.exit(f"Car {args.car_id} has no microphone device. Pass --device-id or seed the database.")
            device_id = mics[0].id

    orch = load_orchestrator()
    now = datetime.now(timezone.utc)
    record_id = str(uuid.uuid4())
    key = f"audio/{args.car_id}/{now:%Y/%m/%d}/{record_id}{args.audio.suffix.lower() or '.wav'}"
    print(f"Clip stored as {store_clip(args.audio, key)}")

    # The record C3's worker would hand over (device_data_records shape).
    record = {"record_id": record_id, "car_id": args.car_id, "device_id": device_id,
              "s3_object_key": key, "received_timestamp": now, "captured_at": now}
    alert_id = orch.handle_record(record)

    from app.db.repositories import AlertRepo
    alert = AlertRepo().get(alert_id)
    print_result(alert["sound_type"], alert["confidence"], alert["is_anomaly"], alert["probs"])
    print(f"Severity   : {alert['severity']}{'  (alert type disabled: stored as suppressed)' if alert.get('suppressed') else ''}")
    print(f"\nAlert {alert_id} created for car {args.car_id}, device {device_id} (status new).")
    print(f"Open it at http://localhost:5173/alerts/{alert_id}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify", help="classify one clip (writes nothing)")
    c.add_argument("audio", type=Path)
    c.set_defaults(func=cmd_classify)
    a = sub.add_parser("alert", help="run the full pipeline and create an alert")
    a.add_argument("audio", type=Path)
    a.add_argument("--car-id", type=int, default=1)
    a.add_argument("--device-id", type=int, default=None, help="default: the car's first microphone")
    a.set_defaults(func=cmd_alert)
    args = ap.parse_args()
    if not args.audio.is_file():
        sys.exit(f"Audio file not found: {args.audio}")
    args.func(args)


if __name__ == "__main__":
    main()
