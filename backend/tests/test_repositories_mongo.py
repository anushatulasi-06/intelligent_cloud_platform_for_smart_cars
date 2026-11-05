"""MongoDB repositories (on mongomock)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.repositories import AlertRepo, PositionRepo, RecordRepo

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def alerts(mongo_db):
    repo = AlertRepo(mongo_db["alerts"])
    rows = [
        (1, "emergency", "high", "new", False, 0), (1, "human", "medium", "acknowledged", True, 1),
        (2, "traffic", "low", "resolved", False, 2), (3, "emergency", "critical", "new", False, 3),
        (3, "in_vehicle", "high", "new", True, 4),
    ]
    for car, sound, sev, status, anomaly, i in rows:
        repo.upsert_by_record_id(f"r{i}", {"car_id": car, "sound_type": sound, "severity": sev, "status": status,
                                           "is_anomaly": anomaly, "timestamp": T0 - timedelta(hours=i)})
    return repo


def test_search_newest_first_and_owner_scope(alerts):
    all_ = alerts.search()
    assert all_["total"] == 5 and [a["record_id"] for a in all_["items"]] == ["r0", "r1", "r2", "r3", "r4"]
    owner = alerts.search(car_ids=[1, 2])
    assert owner["total"] == 3 and {a["car_id"] for a in owner["items"]} == {1, 2}
    assert alerts.search(car_ids=[1, 2], car_id=3)["total"] == 0          # owner cannot widen scope
    assert alerts.search(car_ids=[], page=1)["total"] == 0                # owner with no cars sees nothing


def test_search_filters(alerts):
    assert alerts.search(status="new")["total"] == 3
    assert alerts.search(severity="high", sound_type="emergency")["total"] == 1
    assert alerts.search(is_anomaly=True)["total"] == 2
    assert alerts.search(from_=T0 - timedelta(hours=2), to=T0 - timedelta(hours=1))["total"] == 2


def test_get_scoped(alerts):
    a3 = alerts.get_by_record_id("r3")
    assert alerts.get_scoped(a3["id"], None)["record_id"] == "r3"
    assert alerts.get_scoped(a3["id"], [3])["record_id"] == "r3"
    assert alerts.get_scoped(a3["id"], [1, 2]) is None                    # another owner's alert


def test_upsert_by_record_id_is_idempotent(alerts):
    first = alerts.get_by_record_id("r0")
    again = alerts.upsert_by_record_id("r0", {"car_id": 99, "status": "new", "timestamp": T0})
    assert again["id"] == first["id"] and again["car_id"] == 1            # never overwritten (BR-4.4)
    assert alerts.search()["total"] == 5


def test_update_status(alerts):
    a = alerts.get_by_record_id("r0")
    assert alerts.update_status(a["id"], {"status": "acknowledged", "acknowledged_by": 7})["status"] == "acknowledged"


def test_summary(alerts):
    s = alerts.summary(car_ids=[1, 3])
    assert s["total"] == 4
    assert s["by_status"] == {"new": 3, "acknowledged": 1}
    assert s["by_severity"] == {"high": 2, "medium": 1, "critical": 1}
    assert s["by_type"]["emergency"] == 2


def test_open_counts(alerts):
    assert alerts.open_counts([1, 2, 3]) == {1: {"open": 2, "top": "high"}, 3: {"open": 2, "top": "critical"}}


def test_positions(mongo_db):
    repo = PositionRepo(mongo_db["car_positions"])
    for i in range(10):
        repo.add_position(1, T0 + timedelta(minutes=i), 37.0 + i / 100, -121.0, speed_kmh=30, heading=90)
    repo.add_position(2, T0, 38.0, -122.0)
    last = repo.last_per_car([1, 2])
    assert last[1]["lat"] == pytest.approx(37.09) and last[2]["lat"] == 38.0
    assert repo.latest(1)["ts"] == T0 + timedelta(minutes=9)
    assert set(repo.last_per_car([2])) == {2}                              # scoped
    track = repo.track(1, T0, T0 + timedelta(minutes=9), max_points=4)
    assert len(track) == 4 and track[0]["ts"] == T0 and track[-1]["ts"] == T0 + timedelta(minutes=9)
    assert len(repo.track(1, T0, T0 + timedelta(minutes=3))) == 4


def test_record_queue_lifecycle(mongo_db):
    repo = RecordRepo(mongo_db["device_data_records"])
    for i in range(3):
        repo.create_pending({"record_id": f"rec{i}", "car_id": 1, "device_id": 1,
                             "received_timestamp": T0 + timedelta(seconds=i), "clip_id": None})
    assert "clip_id" not in repo.get_by_record_id("rec0")                 # sparse index: field omitted
    first = repo.claim_next()
    assert first["record_id"] == "rec0" and first["status"] == "processing" and first["attempts"] == 1
    assert repo.claim_next()["record_id"] == "rec1"                       # rec0 is leased
    repo.mark_processed("rec0", "alert123")
    done = repo.get_by_record_id("rec0")
    assert done["processed"] is True and done["alert_id"] == "alert123"


def test_record_expired_lease_is_reclaimed(mongo_db):
    repo = RecordRepo(mongo_db["device_data_records"])
    repo.create_pending({"record_id": "r", "car_id": 1, "device_id": 1})
    assert repo.claim_next(lease_seconds=-1)["attempts"] == 1            # lease already expired
    again = repo.claim_next()
    assert again["record_id"] == "r" and again["attempts"] == 2


def test_record_errors_then_fails_then_manual_retry(mongo_db):
    repo = RecordRepo(mongo_db["device_data_records"])
    repo.create_pending({"record_id": "r", "car_id": 1, "device_id": 1})
    for attempt in (1, 2):
        repo.claim_next()
        assert repo.mark_error("r", "boom")["status"] == "pending"
    repo.claim_next()
    assert repo.mark_error("r", "boom")["status"] == "failed"           # 3rd attempt used up
    assert repo.claim_next() is None
    assert repo.retry_failed("r")["status"] == "pending"
    assert repo.retry_failed("r") is None                                 # not failed any more -> 409


def test_record_search_and_stats(mongo_db):
    repo = RecordRepo(mongo_db["device_data_records"])
    now = T0
    for i, (car, status) in enumerate([(1, "processed"), (1, "failed"), (2, "pending"), (3, "processed")]):
        repo.create_pending({"record_id": f"s{i}", "car_id": car, "device_id": car,
                             "received_timestamp": now - timedelta(hours=i)})
        repo._set(f"s{i}", {"status": status})
    assert repo.search(car_ids=[1, 2])["total"] == 3
    assert repo.search(status="processed")["total"] == 2
    stats = repo.stats(window_hours=12, now=now)
    assert stats["counts"] == {"pending": 1, "processing": 0, "processed": 2, "failed": 1}
    assert len(stats["per_hour"]) == 12 and sum(h["count"] for h in stats["per_hour"]) == 4
