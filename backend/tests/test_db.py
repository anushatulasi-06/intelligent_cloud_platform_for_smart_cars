from datetime import date

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from backend.app.core.security import hash_device_token, hash_password, verify_password
from backend.app.db.init_db import ALERT_TYPES, seed
from backend.app.db.mongo import ALERTS, DEVICE_DATA_RECORDS
from backend.app.models import (
    AlertTypeConfig,
    Car,
    Device,
    DeviceStatus,
    DeviceType,
    ModelVersion,
    Role,
    Subscription,
    User,
)


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_creates_all_tables(db, engine):
    assert set(inspect(engine).get_table_names()) == {
        "users",
        "cars",
        "devices",
        "subscriptions",
        "alert_type_config",
        "model_versions",
    }


def test_creates_mongo_indexes(db, mongo_db):
    alert_keys = [list(ix["key"]) for ix in mongo_db[ALERTS].index_information().values()]
    assert [("car_id", 1), ("timestamp", -1)] in alert_keys
    assert [("status", 1), ("timestamp", -1)] in alert_keys

    record_keys = [list(ix["key"]) for ix in mongo_db[DEVICE_DATA_RECORDS].index_information().values()]
    assert [("processed", 1), ("received_timestamp", 1)] in record_keys


def test_seed_inserts_demo_data_once(db):
    tokens = seed(db, "pw")
    seed(db, "pw")  # second run must not duplicate anything

    assert count(db, User) == 3
    assert {u.role for u in db.scalars(select(User))} == set(Role)
    assert count(db, AlertTypeConfig) == len(ALERT_TYPES)
    assert count(db, Car) == 1
    assert count(db, Subscription) == len(ALERT_TYPES)

    devices = db.scalars(select(Device)).all()
    assert {d.device_type for d in devices} == {DeviceType.audio, DeviceType.camera}
    assert all(d.status == DeviceStatus.offline for d in devices)
    # Only the hash is stored; the plain token is returned once.
    for d in devices:
        assert d.device_token_hash == hash_device_token(tokens[d.serial_number])

    active = db.scalars(select(ModelVersion).where(ModelVersion.is_active)).all()
    assert len(active) == 1 and 0 < active[0].accuracy <= 1


def test_seed_hashes_passwords(db):
    seed(db, "secret-pw")
    user = db.scalar(select(User).where(User.email == "owner@example.com"))
    assert user.password_hash != "secret-pw"
    assert verify_password("secret-pw", user.password_hash)


def test_rejects_unknown_role(db):
    db.add(User(name="x", email="x@example.com", role="admin", password_hash="h"))
    with pytest.raises((IntegrityError, StatementError)):
        db.commit()


def test_rejects_duplicate_subscription_for_same_car_and_type(db):
    seed(db, "pw")
    sub = db.scalar(select(Subscription).limit(1))
    db.add(Subscription(owner_id=sub.owner_id, car_id=sub.car_id, alert_type=sub.alert_type))
    with pytest.raises(IntegrityError):
        db.commit()


def test_rejects_subscription_to_unknown_alert_type(db):
    seed(db, "pw")
    sub = db.scalar(select(Subscription).limit(1))
    db.add(Subscription(owner_id=sub.owner_id, car_id=sub.car_id, alert_type="not_a_type"))
    with pytest.raises(IntegrityError):
        db.commit()


def make_version(label, active):
    return ModelVersion(
        version_label=label,
        trained_date=date(2026, 10, 7),
        accuracy=0.8,
        macro_f1=0.8,
        artifact_path="x.pth",
        is_active=active,
    )


def test_allows_many_inactive_model_versions(db):
    db.add_all([make_version("a", False), make_version("b", False), make_version("c", True)])
    db.commit()
    assert count(db, ModelVersion) == 3


def test_rejects_second_active_model_version(db):
    db.add_all([make_version("a", True), make_version("b", True)])
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_car_deletes_its_devices_and_subscriptions(db):
    seed(db, "pw")
    db.delete(db.scalar(select(Car)))
    db.commit()
    assert count(db, Device) == 0
    assert count(db, Subscription) == 0
    assert count(db, User) == 3


def test_password_hash_round_trip():
    h = hash_password("abc")
    assert verify_password("abc", h)
    assert not verify_password("abd", h)
