"""Development seed data. Idempotent: running it again adds nothing and changes nothing.

Run from backend/ after `alembic upgrade head`:
    python -m app.db.seed

PostgreSQL: 4 users (2 owners, 1 iot_staff, 1 cloud_staff), 4 cars, 5 devices, the 5 alert_type_config rows,
            1 model_versions row: cnn-v1, the trained model, active.
MongoDB:    25 alerts matching the frontend mock data (same ids), indexes ensured first.

Each row is looked up by a natural key (email, plate, car+name, alert_type, version_label,
record_id) and only inserted when missing. Existing rows are never overwritten.

Dev logins (bcrypt-hashed in the database; for local development only):
    owner@autosound.dev / owner-dev-pass        (owner of cars 1-3)
    owner2@autosound.dev / owner2-dev-pass      (owner of car 4)
    iot@autosound.dev   / iot-dev-pass          (iot_staff)
    cloud@autosound.dev / cloud-dev-pass        (cloud_staff)
"""
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import bcrypt
from bson import ObjectId
from pymongo.database import Database
from sqlalchemy.orm import Session

from app.db import mongo
from app.db.models import AlertTypeConfig, Car, Device, ModelVersion, User
from app.db.repositories import (AlertRepo, AlertTypeRepo, CarRepo, DeviceRepo, ModelRepo,
                                 UserRepo)

log = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent / "seed_data"
BCRYPT_ROUNDS = 12          # cost 12, as in the design doc

USERS = [
    {"name": "Olivia Owner", "email": "owner@autosound.dev", "role": "owner", "password": "owner-dev-pass"},
    {"name": "Oscar Owner", "email": "owner2@autosound.dev", "role": "owner", "password": "owner2-dev-pass"},
    {"name": "Rishi Sheth", "email": "iot@autosound.dev", "role": "iot_staff", "password": "iot-dev-pass"},
    {"name": "Chloe Cloud", "email": "cloud@autosound.dev", "role": "cloud_staff", "password": "cloud-dev-pass"},
]

# Same four cars as the frontend mocks (car 1..4, in this order). Two owners, so the demo shows that
# each owner sees only their own cars' alerts. "owner" is the owner's email.
CARS = [
    {"owner": "owner@autosound.dev", "plate": "7ABC123", "make": "Toyota", "model": "Camry",
     "last_lat": 37.3352, "last_lon": -121.8811},
    {"owner": "owner@autosound.dev", "plate": "8DEF456", "make": "Honda", "model": "Civic",
     "last_lat": 37.3496, "last_lon": -121.9390},
    {"owner": "owner@autosound.dev", "plate": "9GHI789", "make": "Tesla", "model": "Model 3",
     "last_lat": 37.3230, "last_lon": -121.9150},
    {"owner": "owner2@autosound.dev", "plate": "6JKL012", "make": "Ford", "model": "Escape",
     "last_lat": 37.3688, "last_lon": -121.9130},
]

# Five devices: one microphone per car, plus a camera on car 1. A fresh seed has no heartbeat
# evidence, so every device starts registered. Only a real edge heartbeat can make it online.
# "car" is the 1-based index into CARS.
DEVICES = [
    {"car": 1, "name": "mic-1", "device_type": "microphone", "status": "registered", "firmware": "1.2.1"},
    {"car": 2, "name": "mic-2", "device_type": "microphone", "status": "registered", "firmware": None},
    {"car": 3, "name": "mic-3", "device_type": "microphone", "status": "registered", "firmware": "1.2.0"},
    {"car": 4, "name": "mic-4", "device_type": "microphone", "status": "registered", "firmware": "1.2.1"},
    {"car": 1, "name": "cam-1", "device_type": "camera", "status": "registered", "firmware": "1.2.1"},
]

ALERT_TYPES = [
    {"alert_type": "emergency", "default_severity": "high", "description": "Siren, ambulance horn, fire truck"},
    {"alert_type": "in_vehicle", "default_severity": "high", "description": "Glass breaking, crash impact, car alarm"},
    {"alert_type": "human", "default_severity": "medium", "description": "Scream, crying baby, shouting"},
    {"alert_type": "traffic", "default_severity": "low", "description": "Horn, tyre screech, engine rev"},
    {"alert_type": "environmental", "default_severity": "low", "description": "Rain, wind, street ambience"},
]

# The trained CNN (model/checkpoints, exported with `scripts/export_model.py --version cnn-v1`).
# accuracy = test-set accuracy from model/checkpoints/evaluation_report.json; threshold from
# model/config.py CONFIDENCE_THRESHOLD. artifact_path is relative to backend/, like MODEL_DIR.
MODEL_VERSIONS = [
    {"version_label": "cnn-v1", "trained_date": date(2026, 10, 29), "accuracy": "0.839",
     "confidence_threshold": "0.75", "is_active": True},
]


def artifact_path(label: str) -> str:
    return f"../model/artifacts/{label}"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def device_key_hash(name: str) -> str:
    """SHA-256 of a fixed dev key; real keys are random and shown once (C2)."""
    return hashlib.sha256(f"dev-device-key-{name}".encode()).hexdigest()


@dataclass
class SeedResult:
    inserted: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, n: int = 1) -> None:
        self.inserted[name] = self.inserted.get(name, 0) + n


def seed_postgres(session: Session) -> tuple[SeedResult, dict[int, int], dict[int, int]]:
    """Insert missing PostgreSQL rows. Returns (result, car index -> car id, car id -> microphone id)."""
    result = SeedResult()
    users, cars_repo, devices_repo = UserRepo(session), CarRepo(session), DeviceRepo(session)

    by_email: dict[str, User] = {}
    for u in USERS:
        user = users.get_by_email(u["email"])
        if user is None:
            user = users.add(User(name=u["name"], email=u["email"], role=u["role"],
                                  password_hash=hash_password(u["password"])))
            result.add("users")
        by_email[u["email"]] = user

    car_ids: dict[int, int] = {}
    for index, c in enumerate(CARS, start=1):
        car = cars_repo.get_by_plate(c["plate"])
        if car is None:
            fields = {k: v for k, v in c.items() if k != "owner"}
            car = cars_repo.add(Car(owner_id=by_email[c["owner"]].id, **fields))
            result.add("cars")
        car_ids[index] = car.id

    mic_for_car: dict[int, int] = {}
    for d in DEVICES:
        car_id = car_ids[d["car"]]
        device = devices_repo.get_by_car_and_name(car_id, d["name"])
        if device is None:
            device = devices_repo.add(Device(
                car_id=car_id, name=d["name"], device_type=d["device_type"], status=d["status"],
                firmware=d["firmware"], wifi_ssid=f"car{d['car']}-hotspot", wifi_security="wpa2", ip_mode="dhcp",
                device_key_hash=device_key_hash(d["name"])))
            result.add("devices")
        if device.device_type == "microphone":
            mic_for_car[car_id] = device.id

    types = AlertTypeRepo(session)
    for t in ALERT_TYPES:
        if types.get(t["alert_type"]) is None:
            types.add(AlertTypeConfig(enabled=True, **t))
            result.add("alert_type_config")

    models = ModelRepo(session)
    for m in MODEL_VERSIONS:
        if models.get_by_label(m["version_label"]) is None:
            # Never insert a second active model: only activate the seed's v3 if none is active yet.
            active = m["is_active"] and models.get_active() is None
            models.add(ModelVersion(
                category="audio_classifier", version_label=m["version_label"], trained_date=m["trained_date"],
                accuracy=Decimal(m["accuracy"]), confidence_threshold=Decimal(m["confidence_threshold"]),
                artifact_path=artifact_path(m["version_label"]), is_active=active))
            result.add("model_versions")

    return result, car_ids, mic_for_car


def load_seed_alerts() -> list[dict]:
    return json.loads((SEED_DIR / "alerts.json").read_text())


def seed_mongo(db: Database, car_ids: dict[int, int], mic_for_car: dict[int, int],
               result: SeedResult | None = None) -> SeedResult:
    """Insert missing alerts (by record_id). The frontend's car numbers 1..4 map to the seeded car ids;
    each alert's device is that car's microphone."""
    result = result or SeedResult()
    alerts = AlertRepo(db[mongo.ALERTS])
    for a in load_seed_alerts():
        car_id = car_ids[a["car_id"]]
        doc = {
            "_id": ObjectId(a["id"]),
            "car_id": car_id,
            "device_id": mic_for_car.get(car_id),
            "sound_type": a["sound_type"],
            "confidence": a["confidence"],
            "probs": a["probs"],
            "is_anomaly": a["is_anomaly"],
            "severity": a["severity"],
            "status": a["status"],
            "model_version": "cnn-v1",          # the only (and active) model version
            "s3_object_key": a["s3_object_key"],
            "lat": a["lat"],
            "lon": a["lon"],
            "timestamp": datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00")),
        }
        if alerts.get_by_record_id(a["record_id"]) is None:
            alerts.upsert_by_record_id(a["record_id"], doc)
            result.add("alerts")
    return result


def run() -> SeedResult:
    from app.db.postgres import session_scope

    with session_scope() as session:
        result, car_ids, mic_for_car = seed_postgres(session)
    mongo.ensure_indexes()
    seed_mongo(mongo.get_db(), car_ids, mic_for_car, result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = run()
    log.info("Seed complete. Inserted: %s", res.inserted or "nothing (already seeded)")
