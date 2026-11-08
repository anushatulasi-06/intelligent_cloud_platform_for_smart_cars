"""/fleet/map and /cars/{id}/track (C1, F9 and F10): the marker decision table DT-MV-1,
owner scoping, filters and the 24 hour limit on a route."""
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.db import mongo, postgres
from app.db.models import Car, User
from app.main import create_app
from app.services.map_service import MAX_TRACK_HOURS, ONLINE_SECONDS, marker_status
from tests.auth_headers import bearer

OWNER_A = bearer("owner", 1)        # cars 10, 11, 12
OWNER_B = bearer("owner", 2)        # car 20
STAFF = bearer("cloud_staff", 4)
IOT = bearer("iot_staff", 3)

NOW = datetime.now(timezone.utc)

# car -> how long ago its last position was recorded
LAST_SEEN = {10: 5, 11: 20, 12: 600, 20: 10}

# (car, sound, severity, status, seconds ago)
ALERTS = [
    (10, "emergency", "critical", "new", 60),        # car 10: urgent
    (11, "traffic", "low", "new", 120),              # car 11: alert
    (11, "human", "medium", "resolved", 180),        # resolved, does not count
    (12, "emergency", "high", "new", 240),           # car 12 is offline anyway
    (20, "environmental", "low", "acknowledged", 90),
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
                   Car(id=12, owner_id=1, make="Tesla", model="Model 3", plate="A12"),
                   Car(id=20, owner_id=2, make="Ford", model="Escape", plate="B20")])
    for car, seconds in LAST_SEEN.items():
        mdb["car_positions"].insert_one({"car_id": car, "ts": NOW - timedelta(seconds=seconds),
                                         "lat": 37.33 + car / 1000, "lon": -121.88,
                                         "speed_kmh": 42.0, "heading": 90.0})
    for i, (car, sound, severity, status, seconds) in enumerate(ALERTS):
        mdb["alerts"].insert_one({
            "_id": ObjectId(), "record_id": f"rec-{i}", "car_id": car, "device_id": car,
            "sound_type": sound, "confidence": 0.9, "is_anomaly": False, "severity": severity,
            "status": status, "lat": 37.34, "lon": -121.89,
            "timestamp": NOW - timedelta(seconds=seconds)})
    return mdb


@pytest.fixture
def client(data):
    with TestClient(create_app()) as c:
        yield c


def cars_on_map(client, headers, **params):
    r = client.get("/fleet/map", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return {c["car_id"]: c for c in r.json()["cars"]}


# ---- DT-MV-1 -----------------------------------------------------------------------------

@pytest.mark.parametrize("age,open_alerts,severity,expected", [
    (None, 0, None, "offline"),                 # never reported a position
    (ONLINE_SECONDS + 1, 3, "critical", "offline"),
    (ONLINE_SECONDS, 0, None, "online"),        # exactly on the limit still counts as online
    (10, 0, None, "online"),
    (10, 1, "low", "alert"),
    (10, 2, "medium", "alert"),
    (10, 1, "high", "urgent"),
    (10, 1, "critical", "urgent"),
])
def test_marker_status(age, open_alerts, severity, expected):
    assert marker_status(age, open_alerts, severity) == expected


# ---- GET /fleet/map ----------------------------------------------------------------------

def test_owner_sees_only_their_own_cars(client):
    assert set(cars_on_map(client, OWNER_A)) == {10, 11, 12}
    assert set(cars_on_map(client, OWNER_B)) == {20}


def test_cloud_staff_sees_every_car(client):
    assert set(cars_on_map(client, STAFF)) == {10, 11, 12, 20}


def test_marker_colours_come_from_the_data(client):
    cars = cars_on_map(client, OWNER_A)
    assert cars[10]["status"] == "urgent" and cars[10]["top_severity"] == "critical"
    assert cars[11]["status"] == "alert" and cars[11]["open_alerts"] == 1
    assert cars[12]["status"] == "offline"          # last position is 10 minutes old


def test_map_row_carries_the_last_position(client):
    car = cars_on_map(client, OWNER_A)[10]
    assert car["name"] == "Car 10 - Camry"
    assert car["speed_kmh"] == 42.0 and car["heading"] == 90.0
    assert car["lat"] == pytest.approx(37.34)
    assert car["position_at"] is not None


def test_a_car_without_positions_is_offline(client):
    with postgres.session_scope() as s:
        s.add(Car(id=13, owner_id=1, make="Kia", model="Niro", plate="A13"))
    car = cars_on_map(client, OWNER_A)[13]
    assert car["status"] == "offline" and car["lat"] is None and car["position_at"] is None


def test_status_filter(client):
    assert set(cars_on_map(client, STAFF, status="offline")) == {12}
    assert set(cars_on_map(client, STAFF, status="urgent")) == {10}


def test_sound_type_filter_looks_at_open_alerts_only(client):
    assert set(cars_on_map(client, STAFF, sound_type="emergency")) == {10, 12}
    assert set(cars_on_map(client, STAFF, sound_type="human")) == set()   # that one is resolved


def test_car_filter(client):
    assert set(cars_on_map(client, OWNER_A, car_id=11)) == {11}


def test_an_owner_cannot_ask_for_another_owners_car(client):
    assert cars_on_map(client, OWNER_A, car_id=20) == {}


@pytest.mark.parametrize("headers,expected", [(None, 401), (IOT, 403)])
def test_map_roles(client, headers, expected):
    assert client.get("/fleet/map", headers=headers).status_code == expected


def test_unknown_filter_value_is_422(client):
    assert client.get("/fleet/map", headers=STAFF, params={"status": "pink"}).status_code == 422


# ---- GET /cars/{car_id}/track ------------------------------------------------------------

def track_params(hours=2):
    return {"from": (NOW - timedelta(hours=hours)).isoformat(), "to": NOW.isoformat()}


def test_track_returns_points_and_alerts(client, mdb):
    for i in range(5):
        mdb["car_positions"].insert_one({"car_id": 10, "ts": NOW - timedelta(minutes=10 + i),
                                         "lat": 37.3 + i / 100, "lon": -121.9, "speed_kmh": 30.0})
    body = client.get("/cars/10/track", headers=OWNER_A, params=track_params()).json()
    assert body["car_id"] == 10
    assert len(body["points"]) == 6                      # five added plus the live one
    stamps = [p["ts"] for p in body["points"]]
    assert stamps == sorted(stamps)                      # oldest first, for playback
    assert [a["sound_type"] for a in body["alerts"]] == ["emergency"]
    assert body["alerts"][0]["lat"] == pytest.approx(37.34)


def test_track_thins_long_routes(client, mdb):
    for i in range(50):
        mdb["car_positions"].insert_one({"car_id": 11, "ts": NOW - timedelta(minutes=i),
                                         "lat": 37.3, "lon": -121.9, "speed_kmh": 10.0})
    body = client.get("/cars/11/track", headers=OWNER_A,
                      params={**track_params(), "max_points": 10}).json()
    assert len(body["points"]) == 10


def test_track_defaults_to_the_last_day(client):
    body = client.get("/cars/10/track", headers=OWNER_A).json()
    assert len(body["points"]) == 1


def test_an_alert_without_a_position_uses_the_nearest_point(client, mdb):
    mdb["alerts"].insert_one({"_id": ObjectId(), "record_id": "no-position", "car_id": 11,
                              "sound_type": "human", "severity": "medium", "status": "new",
                              "is_anomaly": False, "confidence": 0.8,
                              "timestamp": NOW - timedelta(seconds=25)})
    body = client.get("/cars/11/track", headers=OWNER_A, params=track_params()).json()
    placed = next(a for a in body["alerts"] if a["sound_type"] == "human" and a["status"] == "new")
    assert placed["lat"] == pytest.approx(body["points"][0]["lat"])


def test_track_of_another_owners_car_is_404(client):
    assert client.get("/cars/20/track", headers=OWNER_A, params=track_params()).status_code == 404


def test_track_of_an_unknown_car_is_404(client):
    assert client.get("/cars/777/track", headers=STAFF, params=track_params()).status_code == 404


def test_cloud_staff_can_track_any_car(client):
    assert client.get("/cars/20/track", headers=STAFF, params=track_params()).status_code == 200


def test_range_longer_than_a_day_is_422(client):
    params = {"from": (NOW - timedelta(hours=MAX_TRACK_HOURS + 1)).isoformat(), "to": NOW.isoformat()}
    assert client.get("/cars/10/track", headers=OWNER_A, params=params).status_code == 422


def test_backwards_range_is_422(client):
    params = {"from": NOW.isoformat(), "to": (NOW - timedelta(hours=1)).isoformat()}
    assert client.get("/cars/10/track", headers=OWNER_A, params=params).status_code == 422


@pytest.mark.parametrize("headers,expected", [(None, 401), (IOT, 403)])
def test_track_roles(client, headers, expected):
    assert client.get("/cars/10/track", headers=headers,
                      params=track_params()).status_code == expected
