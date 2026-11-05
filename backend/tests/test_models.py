"""Table definitions: columns, defaults and CHECK / UNIQUE constraints (on SQLite; the same DDL is
checked against real PostgreSQL in test_migration_pg.py)."""
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db import postgres
from app.db.models import (AlertTypeConfig, Base, Car, Device, MaintenanceJob, ModelVersion,
                           Subscription, User)


def add(obj):
    with postgres.session_scope() as s:
        s.add(obj)
        s.flush()
        s.expunge(obj)
    return obj


def make_user(email="a@x.dev", role="owner"):
    return add(User(name="A", email=email, role=role, password_hash="h" * 60))


def make_car(owner_id, plate=None):
    return add(Car(owner_id=owner_id, make="Toyota", model="Camry", plate=plate))


def test_all_seven_tables_exist(sql_db):
    assert {n for n in inspect(sql_db).get_table_names() if not n.startswith("test_")} == {
        "users", "cars", "devices", "subscriptions", "alert_type_config", "model_versions", "maintenance_jobs"}


def test_column_types_and_lengths():
    t = Base.metadata.tables
    assert t["users"].c.name.type.length == 80 and t["users"].c.email.type.length == 120
    assert t["users"].c.role.type.length == 12 and t["users"].c.password_hash.type.length == 60
    assert t["cars"].c.make.type.length == 40 and t["cars"].c.plate.type.length == 12
    assert t["devices"].c.name.type.length == 60 and t["devices"].c.device_type.type.length == 10
    assert t["devices"].c.device_key_hash.type.length == 64 and t["devices"].c.firmware.type.length == 20
    assert t["subscriptions"].c.alert_type.type.length == 16 and t["subscriptions"].c.notify_method.type.length == 6
    assert t["alert_type_config"].c.default_severity.type.length == 8
    assert t["alert_type_config"].c.description.type.length == 120
    mv = t["model_versions"].c
    assert (mv.accuracy.type.precision, mv.accuracy.type.scale) == (4, 3)
    assert (mv.confidence_threshold.type.precision, mv.confidence_threshold.type.scale) == (3, 2)
    assert mv.version_label.type.length == 30 and mv.artifact_path.type.length == 200
    assert t["maintenance_jobs"].c.error.type.length == 500
    for table, col in [("users", "created_at"), ("cars", "last_position_at"), ("devices", "last_seen"),
                       ("subscriptions", "updated_at"), ("maintenance_jobs", "started_at")]:
        assert t[table].c[col].type.timezone is True, f"{table}.{col} must be TIMESTAMPTZ"


def test_every_allowed_value_fits_its_column():
    """SQLite ignores VARCHAR lengths, so check them here (PostgreSQL would reject the row)."""
    from app.db.models import constants as k
    t = Base.metadata.tables
    cases = [("users", "role", k.ROLES), ("devices", "device_type", k.DEVICE_TYPES),
             ("devices", "status", k.DEVICE_STATUSES), ("devices", "wifi_security", k.WIFI_SECURITY),
             ("devices", "ip_mode", k.IP_MODES), ("subscriptions", "alert_type", k.ALERT_TYPES),
             ("subscriptions", "notify_method", k.NOTIFY_METHODS),
             ("alert_type_config", "alert_type", k.ALERT_TYPES),
             ("alert_type_config", "default_severity", k.SEVERITIES),
             ("maintenance_jobs", "job_type", k.JOB_TYPES), ("maintenance_jobs", "status", k.JOB_STATUSES)]
    for table, col, values in cases:
        length = t[table].c[col].type.length
        too_long = [v for v in values if len(v) > length]
        assert not too_long, f"{table}.{col} VARCHAR({length}) cannot hold {too_long}"


def test_not_null_columns():
    """Required columns (design doc + team decision 2026-11-04)."""
    t = Base.metadata.tables
    required = {"users": ["name", "email", "role", "password_hash"], "cars": ["owner_id", "make", "model"],
                "devices": ["car_id", "name", "device_key_hash"],
                "subscriptions": ["owner_id", "car_id", "alert_type"]}
    for table, cols in required.items():
        for col in cols:
            assert t[table].c[col].nullable is False, f"{table}.{col} must be NOT NULL"
    assert not t["users"].c.name.nullable and not t["users"].c.email.nullable
    assert not t["users"].c.password_hash.nullable
    assert not t["cars"].c.owner_id.nullable and t["cars"].c.plate.nullable
    assert not t["devices"].c.name.nullable


def test_defaults(sql_db):
    u = make_user()
    car = make_car(u.id)
    with postgres.session_scope() as s:
        d = Device(car_id=car.id, name="mic", device_type="microphone", device_key_hash="k" * 64)
        sub = Subscription(owner_id=u.id, car_id=car.id, alert_type="human")
        atc = AlertTypeConfig(alert_type="human", default_severity="medium")
        mv = ModelVersion(version_label="cnn-x")
        s.add_all([d, sub, atc, mv])
        s.flush()
        for o in (d, sub, atc, mv):
            s.refresh(o)
        assert d.status == "registered" and d.created_at is not None and d.updated_at is not None
        assert sub.enabled is True and sub.notify_method == "email"
        assert atc.enabled is True
        assert mv.category == "audio_classifier" and mv.confidence_threshold == Decimal("0.75")
        assert mv.is_active is False


@pytest.mark.parametrize("obj_factory", [
    lambda ids: User(name="B", email="b@x.dev", role="admin", password_hash="h"),
    lambda ids: Device(car_id=ids["car"], name="d", device_type="speaker", device_key_hash="k" * 64),
    lambda ids: Device(car_id=ids["car"], name="d", device_type="camera", status="broken", device_key_hash="k" * 64),
    lambda ids: Device(car_id=ids["car"], name="d", device_type="camera", wifi_security="wep", device_key_hash="k" * 64),
    lambda ids: Device(car_id=ids["car"], name="d", device_type="camera", ip_mode="auto", device_key_hash="k" * 64),
    lambda ids: Subscription(owner_id=ids["user"], car_id=ids["car"], alert_type="music"),
    lambda ids: Subscription(owner_id=ids["user"], car_id=ids["car"], alert_type="human", notify_method="sms"),
    lambda ids: AlertTypeConfig(alert_type="human", default_severity="urgent"),
    lambda ids: ModelVersion(version_label="t1", confidence_threshold=Decimal("0.49")),
    lambda ids: ModelVersion(version_label="t2", confidence_threshold=Decimal("0.96")),
    lambda ids: MaintenanceJob(job_type="vacuum", status="queued"),
    lambda ids: MaintenanceJob(job_type="backup", status="paused"),
], ids=["role", "device_type", "device_status", "wifi_security", "ip_mode", "sub_alert_type",
        "notify_method", "severity", "threshold_low", "threshold_high", "job_type", "job_status"])
def test_check_constraints_reject_bad_values(sql_db, obj_factory):
    u = make_user()
    car = make_car(u.id)
    with pytest.raises(IntegrityError):
        add(obj_factory({"user": u.id, "car": car.id}))


def test_threshold_bounds_inclusive(sql_db):
    add(ModelVersion(version_label="lo", confidence_threshold=Decimal("0.50")))
    add(ModelVersion(version_label="hi", confidence_threshold=Decimal("0.95")))


def test_unique_constraints(sql_db):
    u = make_user()
    with pytest.raises(IntegrityError):
        make_user(email="a@x.dev")
    car = make_car(u.id, plate="P1")
    with pytest.raises(IntegrityError):
        make_car(u.id, plate="P1")
    make_car(u.id, plate=None)
    make_car(u.id, plate=None)                       # several NULL plates are allowed
    add(Device(car_id=car.id, name="mic", device_type="microphone", device_key_hash="k" * 64))
    with pytest.raises(IntegrityError):
        add(Device(car_id=car.id, name="mic", device_type="camera", device_key_hash="k" * 64))
    add(Subscription(owner_id=u.id, car_id=car.id, alert_type="human"))
    with pytest.raises(IntegrityError):
        add(Subscription(owner_id=u.id, car_id=car.id, alert_type="human"))
    add(ModelVersion(version_label="cnn-v1"))
    with pytest.raises(IntegrityError):
        add(ModelVersion(version_label="cnn-v1"))


def test_at_most_one_active_model(sql_db):
    add(ModelVersion(version_label="a", is_active=True))
    add(ModelVersion(version_label="b", is_active=False))
    add(ModelVersion(version_label="c", is_active=False))      # many inactive rows are fine
    with pytest.raises(IntegrityError):
        add(ModelVersion(version_label="d", is_active=True))


def test_cars_need_existing_owner(sql_db):
    with pytest.raises(IntegrityError):
        make_car(owner_id=999)


def test_subscriptions_cascade_on_user_and_car_delete(sql_db):
    u = make_user()
    car = make_car(u.id)
    add(Subscription(owner_id=u.id, car_id=car.id, alert_type="human"))
    with postgres.session_scope() as s:
        s.delete(s.get(Car, car.id))
    with postgres.session_scope() as s:
        assert s.scalars(select(Subscription)).all() == []


def test_maintenance_job_uuid_and_json(sql_db):
    u = make_user()
    job = add(MaintenanceJob(job_type="retention", status="queued", created_by=u.id,
                             params={"collection": "alerts", "older_than_days": 90}))
    with postgres.session_scope() as s:
        got = s.get(MaintenanceJob, job.id)
        assert got.params == {"collection": "alerts", "older_than_days": 90}
        assert len(str(got.id)) == 36


@pytest.mark.parametrize("factory", [
    lambda ids: User(name="X", email="x@x.dev", role=None, password_hash="h"),
    lambda ids: Car(owner_id=ids["user"], make=None, model="Camry"),
    lambda ids: Car(owner_id=ids["user"], make="Toyota", model=None),
    lambda ids: Device(car_id=None, name="d", device_type="camera", device_key_hash="k" * 64),
    lambda ids: Device(car_id=ids["car"], name="d", device_type="camera", device_key_hash=None),
    lambda ids: Subscription(owner_id=None, car_id=ids["car"], alert_type="human"),
    lambda ids: Subscription(owner_id=ids["user"], car_id=None, alert_type="human"),
    lambda ids: Subscription(owner_id=ids["user"], car_id=ids["car"], alert_type=None),
], ids=["users.role", "cars.make", "cars.model", "devices.car_id", "devices.device_key_hash",
        "subscriptions.owner_id", "subscriptions.car_id", "subscriptions.alert_type"])
def test_not_null_rejects_missing_values(sql_db, factory):
    u = make_user()
    car = make_car(u.id)
    with pytest.raises(IntegrityError):
        add(factory({"user": u.id, "car": car.id}))
