"""Create PostgreSQL tables and MongoDB indexes, and optionally load demo data.

From the repo root:
    python -m backend.app.db.init_db            # tables + indexes only
    python -m backend.app.db.init_db --seed     # also demo users, car, devices, catalog
"""
import argparse
import json
from datetime import date
from pathlib import Path

from pymongo.database import Database
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.app import models  # noqa: F401  (registers tables on Base.metadata)
from backend.app.core.config import settings
from backend.app.core.security import hash_device_token, hash_password, new_device_token
from backend.app.db.mongo import ensure_indexes, get_mongo
from backend.app.db.postgres import Base, SessionLocal, engine
from backend.app.models import (
    AlertTypeConfig,
    Car,
    Device,
    DevicePosition,
    DeviceType,
    ModelVersion,
    NotifyMethod,
    Role,
    Severity,
    Subscription,
    User,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKPOINT_DIR = REPO_ROOT / "model" / "checkpoints"

# One alert type per model category (model/checkpoints/labels.json).
ALERT_TYPES = [
    ("emergency", Severity.high, "Emergency vehicle siren nearby"),
    ("in_vehicle", Severity.critical, "Crash or glass breaking at the vehicle"),
    ("human", Severity.high, "Scream or vocal distress"),
    ("traffic", Severity.low, "Road traffic noise"),
    ("environmental", Severity.low, "Environmental sound (wind, rain, thunder, ...)"),
]


def create_all(pg_engine: Engine, mongo_db: Database) -> None:
    Base.metadata.create_all(pg_engine)
    ensure_indexes(mongo_db)


def seed(db: Session, password: str) -> dict[str, str]:
    """Insert demo data if absent. Returns {serial_number: device_token} for new devices."""
    if db.scalar(select(AlertTypeConfig).limit(1)) is None:
        db.add_all(
            AlertTypeConfig(alert_type=t, default_severity=s, description=d) for t, s, d in ALERT_TYPES
        )

    if db.scalar(select(ModelVersion).limit(1)) is None:
        report = json.loads((CHECKPOINT_DIR / "evaluation_report.json").read_text())["classification"]
        db.add(
            ModelVersion(
                version_label="cnn-5class-v1",
                trained_date=date(2026, 10, 7),
                accuracy=round(report["accuracy"], 4),
                macro_f1=round(report["macro_f1"], 4),
                artifact_path="model/checkpoints/cnn_weights.pth",
                is_active=True,
            )
        )

    tokens: dict[str, str] = {}
    if db.scalar(select(User).limit(1)) is None:
        pw = hash_password(password)
        owner = User(name="Demo Owner", email="owner@example.com", role=Role.owner, password_hash=pw)
        db.add_all(
            [
                owner,
                User(name="Demo IoT Staff", email="iot@example.com", role=Role.iot_staff, password_hash=pw),
                User(name="Demo Cloud Staff", email="cloud@example.com", role=Role.cloud_staff, password_hash=pw),
            ]
        )
        car = Car(owner=owner, make="Tesla", model="Model 3")
        db.add(car)

        for name, dtype, position, serial in [
            ("Cabin mic", DeviceType.audio, DevicePosition.cabin, "MIC-0001"),
            ("Front camera", DeviceType.camera, DevicePosition.front, "CAM-0001"),
        ]:
            token = new_device_token()
            tokens[serial] = token
            db.add(
                Device(
                    car=car,
                    name=name,
                    device_type=dtype,
                    position=position,
                    serial_number=serial,
                    wifi_config={"ssid": "carla-net", "security": "wpa2", "upload_interval_s": 5},
                    device_token_hash=hash_device_token(token),
                )
            )

        db.add_all(
            Subscription(owner=owner, car=car, alert_type=t, notify_method=NotifyMethod.in_app)
            for t, _, _ in ALERT_TYPES
        )

    db.commit()
    return tokens


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", action="store_true", help="also insert demo data")
    args = parser.parse_args()

    create_all(engine, get_mongo())
    print("Created PostgreSQL tables and MongoDB indexes.")

    if args.seed:
        with SessionLocal() as db:
            tokens = seed(db, settings.seed_password)
        print("Seeded demo data (users: owner@ / iot@ / cloud@example.com, password from SEED_PASSWORD).")
        for serial, token in tokens.items():
            print(f"  device {serial} token (shown once): {token}")


if __name__ == "__main__":
    main()
