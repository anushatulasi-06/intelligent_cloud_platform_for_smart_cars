"""Seed data: counts, links, and idempotency (SQLite + mongomock)."""
import json

import bcrypt
from sqlalchemy import func, select

from app.db import postgres, seed
from app.db.models import AlertTypeConfig, Car, Device, ModelVersion, User
from app.db.seed import SEED_DIR, seed_mongo, seed_postgres

TABLES = (User, Car, Device, AlertTypeConfig, ModelVersion)


def counts(session):
    return {m.__tablename__: session.scalar(select(func.count()).select_from(m)) for m in TABLES}


def run_seed(mongo_db):
    with postgres.session_scope() as s:
        result, car_ids, mics = seed_postgres(s)
    seed_mongo(mongo_db, car_ids, mics, result)
    return result


def test_seed_inserts_expected_rows(sql_db, mongo_db):
    result = run_seed(mongo_db)
    assert result.inserted == {"users": 4, "cars": 4, "devices": 5, "alert_type_config": 5,
                               "model_versions": 1, "alerts": 25}
    with postgres.session_scope() as s:
        assert counts(s) == {"users": 4, "cars": 4, "devices": 5, "alert_type_config": 5, "model_versions": 1}
        assert sorted(u.role for u in s.scalars(select(User))) == ["cloud_staff", "iot_staff", "owner", "owner"]
        sev = {t.alert_type: t.default_severity for t in s.scalars(select(AlertTypeConfig))}
        assert sev == {"emergency": "high", "in_vehicle": "high", "human": "medium", "traffic": "low",
                       "environmental": "low"}
        [model] = s.scalars(select(ModelVersion)).all()
        assert (model.version_label, str(model.accuracy), str(model.confidence_threshold), model.is_active) == \
            ("cnn-v1", "0.839", "0.75", True)
        assert model.artifact_path == "../model/artifacts/cnn-v1"
        devices = list(s.scalars(select(Device)))
        assert sorted(d.device_type for d in devices) == ["camera"] + ["microphone"] * 4
        assert all(len(d.device_key_hash) == 64 for d in devices)


def test_seed_is_idempotent(sql_db, mongo_db):
    run_seed(mongo_db)
    with postgres.session_scope() as s:
        before = counts(s)
        hashes = {u.email: u.password_hash for u in s.scalars(select(User))}
    second = run_seed(mongo_db)
    assert second.inserted == {}
    with postgres.session_scope() as s:
        assert counts(s) == before
        assert {u.email: u.password_hash for u in s.scalars(select(User))} == hashes   # not re-hashed
    assert mongo_db["alerts"].count_documents({}) == 25


def test_passwords_are_bcrypt(sql_db, mongo_db):
    run_seed(mongo_db)
    with postgres.session_scope() as s:
        owner = s.scalar(select(User).where(User.email == "owner@autosound.dev"))
        assert owner.password_hash.startswith("$2b$12$") and len(owner.password_hash) == 60
        assert bcrypt.checkpw(b"owner-dev-pass", owner.password_hash.encode())
        assert not bcrypt.checkpw(b"wrong", owner.password_hash.encode())


def test_alerts_match_frontend_mock_and_link_to_seeded_rows(sql_db, mongo_db):
    run_seed(mongo_db)
    source = {a["id"]: a for a in json.loads((SEED_DIR / "alerts.json").read_text())}
    with postgres.session_scope() as s:
        car_ids = set(s.scalars(select(Car.id)))
        mic_ids = {d.car_id: d.id for d in s.scalars(select(Device).where(Device.device_type == "microphone"))}
    docs = list(mongo_db["alerts"].find())
    assert len(docs) == 25 and sum(d["is_anomaly"] for d in docs) == 3
    expected_fields = {"_id", "record_id", "car_id", "device_id", "sound_type", "confidence", "probs", "is_anomaly",
                       "severity", "status", "model_version", "s3_object_key", "lat", "lon", "timestamp"}
    for d in docs:
        assert set(d) == expected_fields
        src = source[str(d["_id"])]                                    # same ids as the frontend mock
        assert d["model_version"] == "cnn-v1"
        for k in ("record_id", "sound_type", "confidence", "probs", "severity", "status", "s3_object_key"):
            assert d[k] == src[k]
        assert d["car_id"] in car_ids and d["device_id"] == mic_ids[d["car_id"]]
        assert d["timestamp"].isoformat().startswith(src["timestamp"][:19])


def test_seed_does_not_add_second_active_model(sql_db, mongo_db):
    with postgres.session_scope() as s:
        s.add(ModelVersion(version_label="cnn-v9", is_active=True))
    run_seed(mongo_db)
    with postgres.session_scope() as s:
        active = [m.version_label for m in s.scalars(select(ModelVersion).where(ModelVersion.is_active.is_(True)))]
    assert active == ["cnn-v9"]


def test_seeded_active_model_loads_from_artifacts(sql_db, mongo_db):
    """The seed's active version points at real artifacts the orchestrator can load."""
    import pytest
    from app.config import BACKEND_DIR
    if not (BACKEND_DIR.parent / "model" / "artifacts" / "cnn-v1" / "model_weights.pt").is_file():
        pytest.skip("model/artifacts/cnn-v1 not exported")
    from app.services.orchestrator import MLOrchestrator
    run_seed(mongo_db)
    loaded = MLOrchestrator().reload_model()
    assert loaded.version_label == "cnn-v1" and loaded.threshold == 0.75
    assert loaded.classifier.config["dummy"] is False


def test_two_owners_split_the_cars(sql_db, mongo_db):
    from app.db.repositories import AlertRepo, CarRepo, UserRepo
    run_seed(mongo_db)
    with postgres.session_scope() as s:
        o1 = UserRepo(s).get_by_email("owner@autosound.dev")
        o2 = UserRepo(s).get_by_email("owner2@autosound.dev")
        cars1, cars2 = CarRepo(s).cars_of_owner(o1.id), CarRepo(s).cars_of_owner(o2.id)
        assert len(cars1) == 3 and len(cars2) == 1 and not set(cars1) & set(cars2)
        assert CarRepo(s).get(cars2[0]).plate == "6JKL012"
        assert bcrypt.checkpw(b"owner2-dev-pass", o2.password_hash.encode())
    alerts = AlertRepo(mongo_db["alerts"])
    n1, n2 = alerts.search(car_ids=cars1)["total"], alerts.search(car_ids=cars2)["total"]
    assert n1 > 0 and n2 > 0 and n1 + n2 == 25
