"""PostgreSQL repositories (on SQLite)."""
from decimal import Decimal

import pytest

from app.db.models import Car, Device, ModelVersion, User
from app.db.repositories import (AlertTypeRepo, CarRepo, DeviceRepo, JobRepo, ModelRepo,
                                 SubscriptionRepo, UserRepo)


@pytest.fixture
def world(session):
    """Two owners: owner A has cars 1-2, owner B has car 3; devices on each."""
    users = UserRepo(session)
    a = users.add(User(name="A", email="A@Example.dev ", role="owner", password_hash="h"))
    b = users.add(User(name="B", email="b@example.dev", role="owner", password_hash="h"))
    staff = users.add(User(name="S", email="s@example.dev", role="cloud_staff", password_hash="h"))
    cars = CarRepo(session)
    c1 = cars.add(Car(owner_id=a.id, make="Toyota", model="Camry", plate="A1"))
    c2 = cars.add(Car(owner_id=a.id, make="Toyota", model="Camry", plate="A2"))
    c3 = cars.add(Car(owner_id=b.id, make="Toyota", model="Camry", plate="B1"))
    devs = DeviceRepo(session)
    for car, name, dtype, status in [(c1, "mic-a1", "microphone", "online"), (c1, "cam-a1", "camera", "offline"),
                                     (c2, "mic-a2", "microphone", "online"), (c3, "mic-b1", "microphone", "online")]:
        devs.add(Device(car_id=car.id, name=name, device_type=dtype, status=status, device_key_hash="k" * 64))
    return {"a": a, "b": b, "staff": staff, "c1": c1, "c2": c2, "c3": c3, "session": session}


def test_user_email_lowercase_and_lookup(world):
    users = UserRepo(world["session"])
    assert world["a"].email == "a@example.dev"
    assert users.get_by_email("  A@EXAMPLE.DEV").id == world["a"].id
    assert [u.email for u in users.list_by_role("owner")] == ["a@example.dev", "b@example.dev"]


def test_car_owner_scoping(world):
    cars = CarRepo(world["session"])
    a, b = world["a"], world["b"]
    assert cars.ids_for_owner(a.id) == [world["c1"].id, world["c2"].id]
    assert [c.plate for c in cars.list_for_owner(b.id)] == ["B1"]
    assert cars.is_owned_by(world["c1"].id, a.id) and not cars.is_owned_by(world["c3"].id, a.id)


def test_car_update_position(world):
    from datetime import datetime, timezone
    at = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
    car = CarRepo(world["session"]).update_position(world["c1"].id, 37.1, -121.9, at)
    assert (car.last_lat, car.last_lon) == (37.1, -121.9)


def test_device_search_owner_scoped_in_query(world):
    devs = DeviceRepo(world["session"])
    page = devs.search(owner_id=world["a"].id)
    assert page["total"] == 3 and {d.name for d in page["items"]} == {"mic-a1", "cam-a1", "mic-a2"}
    assert devs.search(owner_id=world["b"].id)["total"] == 1
    assert devs.search()["total"] == 4                                  # staff: all devices
    assert devs.search(status="offline")["total"] == 1
    assert devs.search(device_type="microphone", q="A1")["items"][0].name == "mic-a1"
    assert devs.search(owner_id=world["b"].id, car_id=world["c1"].id)["total"] == 0   # cannot widen scope


def test_device_get_for_owner(world):
    devs = DeviceRepo(world["session"])
    mic_b = devs.get_by_car_and_name(world["c3"].id, "mic-b1")
    assert devs.get_for_owner(mic_b.id, world["b"].id).id == mic_b.id
    assert devs.get_for_owner(mic_b.id, world["a"].id) is None


def test_subscription_upsert_and_is_enabled(world):
    subs = SubscriptionRepo(world["session"])
    a, c1 = world["a"], world["c1"]
    row, created = subs.upsert(a.id, c1.id, "emergency", True)
    assert created and row.notify_method == "email"
    row2, created2 = subs.upsert(a.id, c1.id, "emergency", False, "in_app")
    assert not created2 and row2.id == row.id and row2.enabled is False and row2.notify_method == "in_app"
    assert subs.is_enabled(c1.id, "emergency") == (False, "in_app")
    subs.upsert(a.id, c1.id, "human", True)
    assert subs.is_enabled(c1.id, "human") == (True, "email")
    assert subs.is_enabled(c1.id, "traffic") == (False, None)            # missing row = do not notify
    assert len(subs.list_for_owner(a.id)) == 2 and subs.list_for_owner(world["b"].id) == []
    assert subs.get_for_owner(row.id, world["b"].id) is None


def test_subscription_of_another_user_does_not_notify(world):
    """Only the car owner's subscription counts."""
    subs = SubscriptionRepo(world["session"])
    subs.upsert(world["b"].id, world["c1"].id, "human", True)            # B subscribed to A's car
    assert subs.is_enabled(world["c1"].id, "human") == (False, None)


def test_alert_type_list_and_update(session):
    from app.db.models import AlertTypeConfig
    repo = AlertTypeRepo(session)
    for t, sev in [("traffic", "low"), ("emergency", "high")]:
        repo.add(AlertTypeConfig(alert_type=t, default_severity=sev))
    assert [t.alert_type for t in repo.list_all()] == ["emergency", "traffic"]
    assert repo.update("traffic", {"default_severity": "medium"}).default_severity == "medium"


def test_model_activate_and_threshold(session):
    repo = ModelRepo(session)
    v1 = repo.add(ModelVersion(version_label="cnn-v1", is_active=False))
    v2 = repo.add(ModelVersion(version_label="cnn-v2", is_active=True))
    assert repo.get_active().id == v2.id
    assert repo.activate(v1.id).is_active is True
    session.refresh(v2)
    assert v2.is_active is False and repo.get_active().id == v1.id
    assert repo.activate(999) is None
    assert repo.set_threshold(v1.id, 0.8).confidence_threshold == Decimal("0.80")
    for bad in (0.49, 0.951):
        with pytest.raises(ValueError):
            repo.set_threshold(v1.id, bad)
    assert [m.version_label for m in repo.list_all()] == ["cnn-v2", "cnn-v1"]
    assert repo.get_by_label("cnn-v2").id == v2.id


def test_job_lifecycle(world):
    jobs = JobRepo(world["session"])
    job = jobs.create("backup", {"target": "s3"}, created_by=world["staff"].id)
    assert job.status == "queued" and jobs.has_unfinished("backup") and not jobs.has_unfinished("retention")
    jobs.mark_running(job.id)
    assert jobs.get(job.id).started_at is not None and jobs.has_unfinished("backup")
    jobs.mark_succeeded(job.id, {"size_bytes": 123})
    assert jobs.get(job.id).result == {"size_bytes": 123} and not jobs.has_unfinished("backup")
    failed = jobs.create("retention")
    jobs.mark_failed(failed.id, "x" * 600)
    assert len(jobs.get(failed.id).error) == 500
    assert {j.job_type for j in jobs.list_recent()} == {"backup", "retention"}
    assert [j.job_type for j in jobs.list_recent("backup")] == ["backup"]
