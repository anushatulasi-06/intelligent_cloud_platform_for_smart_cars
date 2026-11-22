"""/dashboard/owner, /admin/fleet-summary and /admin/reports/alerts (C1, F2 and F6):
roles, owner scoping, counters and the report groupings."""
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.db import mongo, postgres
from app.db.models import Car, Device, User
from app.main import create_app
from tests.auth_headers import bearer

OWNER_A = bearer("owner", 1)        # cars 10, 11
OWNER_B = bearer("owner", 2)        # car 20
STAFF = bearer("cloud_staff", 4)
IOT = bearer("iot_staff", 3)

NOW = datetime.now(timezone.utc)
TODAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0)

# (car, sound, severity, status, anomaly, timestamp)
ALERTS = [
    (10, "emergency", "high", "new", False, TODAY + timedelta(hours=1)),
    (10, "human", "medium", "acknowledged", True, TODAY + timedelta(hours=2)),
    (10, "traffic", "low", "resolved", False, TODAY + timedelta(hours=3)),
    (11, "in_vehicle", "high", "new", False, TODAY + timedelta(hours=4)),
    (11, "emergency", "critical", "new", True, TODAY - timedelta(days=1)),
    (11, "traffic", "low", "resolved", False, TODAY - timedelta(days=2)),
    (20, "environmental", "low", "new", False, TODAY + timedelta(hours=5)),
    (20, "emergency", "high", "acknowledged", False, TODAY - timedelta(days=3)),
]

DEVICES = [
    (10, "mic-10", "online"), (10, "cam-10", "online"),
    (11, "mic-11", "offline"),
    (20, "mic-20", "registered"),
]


@pytest.fixture
def mdb(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["autosound_test"]
    monkeypatch.setattr(mongo, "get_db", lambda: db)
    return db


@pytest.fixture
def data(sql_db, mdb):
    with postgres.session_scope() as s:
        s.add_all([User(id=1, name="A", email="a@x.dev", role="owner", password_hash="h"),
                   User(id=2, name="B", email="b@x.dev", role="owner", password_hash="h"),
                   User(id=3, name="I", email="i@x.dev", role="iot_staff", password_hash="h"),
                   User(id=4, name="C", email="c@x.dev", role="cloud_staff", password_hash="h")])
        s.flush()
        s.add_all([Car(id=10, owner_id=1, make="Toyota", model="Camry", plate="A10"),
                   Car(id=11, owner_id=1, make="Honda", model="Civic", plate="A11"),
                   Car(id=20, owner_id=2, make="Ford", model="Escape", plate="B20")])
        s.flush()
        s.add_all([Device(car_id=car, name=name, device_type="microphone", status=status,
                          device_key_hash="x" * 64) for car, name, status in DEVICES])
    for i, (car, sound, severity, status, anomaly, ts) in enumerate(ALERTS):
        mdb["alerts"].insert_one({
            "_id": ObjectId(), "record_id": f"rec-{i}", "car_id": car, "device_id": car,
            "sound_type": sound, "confidence": 0.9, "is_anomaly": anomaly, "severity": severity,
            "status": status, "timestamp": ts})
    return mdb


@pytest.fixture
def client(data):
    with TestClient(create_app()) as c:
        yield c


# ---- GET /dashboard/owner ----------------------------------------------------------------

def test_owner_dashboard_lists_only_the_callers_cars(client):
    body = client.get("/dashboard/owner", headers=OWNER_A).json()
    assert [c["id"] for c in body["cars"]] == [10, 11]
    assert {a["car_id"] for a in body["recent_alerts"]} == {10, 11}


def test_owner_dashboard_car_rows(client):
    cars = {c["id"]: c for c in client.get("/dashboard/owner", headers=OWNER_A).json()["cars"]}
    assert cars[10] == {"id": 10, "make": "Toyota", "model": "Camry", "devices_total": 2,
                        "devices_online": 2, "status": "online"}
    assert cars[11]["devices_online"] == 0 and cars[11]["status"] == "offline"


def test_owner_dashboard_counts(client):
    counts = client.get("/dashboard/owner", headers=OWNER_A).json()["counts"]
    assert counts["alerts_today"] == 4          # the four of today on cars 10 and 11
    assert counts["unresolved"] == 4            # new + acknowledged, any day


def test_owner_dashboard_shows_the_five_newest_alerts(client):
    recent = client.get("/dashboard/owner", headers=OWNER_A).json()["recent_alerts"]
    assert len(recent) == 5
    stamps = [a["timestamp"] for a in recent]
    assert stamps == sorted(stamps, reverse=True)


def test_an_owner_with_no_cars_gets_empty_lists(client):
    with postgres.session_scope() as s:
        s.add(User(id=8, name="N", email="n@x.dev", role="owner", password_hash="h"))
    body = client.get("/dashboard/owner", headers=bearer("owner", 8)).json()
    assert body == {"cars": [], "counts": {"alerts_today": 0, "unresolved": 0}, "recent_alerts": []}


@pytest.mark.parametrize("headers,expected", [(None, 401), (STAFF, 403), (IOT, 403)])
def test_owner_dashboard_roles(client, headers, expected):
    assert client.get("/dashboard/owner", headers=headers).status_code == expected


# ---- GET /admin/fleet-summary ------------------------------------------------------------

def test_fleet_summary_counts_the_whole_fleet(client):
    body = client.get("/admin/fleet-summary", headers=STAFF).json()
    assert body["total_cars"] == 3
    assert body["devices"] == {"registered": 1, "online": 2, "offline": 1, "configuring": 0}
    assert body["alerts_today"] == 5            # every car, today only
    assert body["anomalies_today"] == 1


def test_fleet_summary_window(client):
    body = client.get("/admin/fleet-summary", headers=STAFF, params={"window_days": 2}).json()
    assert [d["date"] for d in body["alerts_per_day"]] == [
        (TODAY - timedelta(days=1)).strftime("%Y-%m-%d"), TODAY.strftime("%Y-%m-%d")]
    assert [d["count"] for d in body["alerts_per_day"]] == [1, 5]
    assert body["alerts_by_type"] == {"emergency": 2, "human": 1, "traffic": 1,
                                      "in_vehicle": 1, "environmental": 1}


def test_fleet_summary_fills_quiet_days_with_zero(client):
    body = client.get("/admin/fleet-summary", headers=STAFF, params={"window_days": 7}).json()
    assert len(body["alerts_per_day"]) == 7
    assert body["alerts_per_day"][0]["count"] == 0


@pytest.mark.parametrize("days", [0, -1, 91])
def test_fleet_summary_window_out_of_range(client, days):
    r = client.get("/admin/fleet-summary", headers=STAFF, params={"window_days": days})
    assert r.status_code == 422


@pytest.mark.parametrize("headers,expected", [(None, 401), (OWNER_A, 403), (IOT, 403)])
def test_fleet_summary_roles(client, headers, expected):
    assert client.get("/admin/fleet-summary", headers=headers).status_code == expected


# ---- GET /admin/reports/alerts -----------------------------------------------------------

def window(days_back=10):
    return {"from": (TODAY - timedelta(days=days_back)).isoformat(),
            "to": (TODAY + timedelta(days=1)).isoformat()}


def test_report_grouped_by_sound_type(client):
    body = client.get("/admin/reports/alerts", headers=STAFF, params=window()).json()
    assert body["group_by"] == "sound_type"
    rows = {r["key"]: r for r in body["rows"]}
    assert rows["emergency"]["count"] == 3
    assert rows["emergency"]["anomalies"] == 1
    assert rows["emergency"]["avg_confidence"] == 0.9


def test_report_other_groupings(client):
    for group_by, expected in [("severity", {"high", "medium", "low", "critical"}),
                               ("status", {"new", "acknowledged", "resolved"}),
                               ("car", {"10", "11", "20"})]:
        body = client.get("/admin/reports/alerts", headers=STAFF,
                          params={**window(), "group_by": group_by}).json()
        assert {r["key"] for r in body["rows"]} == expected


def test_report_can_be_limited_to_one_car(client):
    body = client.get("/admin/reports/alerts", headers=STAFF,
                      params={**window(), "group_by": "car", "car_id": 10}).json()
    assert body["rows"] == [{"key": "10", "count": 3, "anomalies": 1, "avg_confidence": 0.9}]


def test_report_needs_both_dates(client):
    assert client.get("/admin/reports/alerts", headers=STAFF).status_code == 422
    assert client.get("/admin/reports/alerts", headers=STAFF,
                      params={"from": TODAY.isoformat()}).status_code == 422


def test_report_rejects_a_long_or_backwards_range(client):
    long_range = {"from": (TODAY - timedelta(days=120)).isoformat(), "to": TODAY.isoformat()}
    assert client.get("/admin/reports/alerts", headers=STAFF, params=long_range).status_code == 422
    backwards = {"from": TODAY.isoformat(), "to": (TODAY - timedelta(days=1)).isoformat()}
    assert client.get("/admin/reports/alerts", headers=STAFF, params=backwards).status_code == 422


def test_report_rejects_an_unknown_grouping(client):
    r = client.get("/admin/reports/alerts", headers=STAFF, params={**window(), "group_by": "device"})
    assert r.status_code == 422


@pytest.mark.parametrize("headers,expected", [(None, 401), (OWNER_A, 403), (IOT, 403)])
def test_report_roles(client, headers, expected):
    assert client.get("/admin/reports/alerts", headers=headers,
                      params=window()).status_code == expected
