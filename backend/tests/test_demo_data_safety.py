"""The opt-in demo feed must never replace telemetry or rewrite alert history."""
import importlib.util
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db import mongo, postgres
from app.db.models import Car, User
from app.db.repositories import PositionRepo

spec = importlib.util.spec_from_file_location(
    "demo_data_safety", Path(__file__).resolve().parents[2] / "scripts" / "demo_data.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def feed_db(sql_db, mongo_db, monkeypatch):
    monkeypatch.setattr(mongo, "get_db", lambda: mongo_db)
    with postgres.session_scope() as session:
        session.add(User(id=1, name="Test owner", email="test@example.invalid", role="owner",
                         password_hash="not-a-login"))
        session.flush()
        session.add_all([
            Car(id=10, owner_id=1, plate=demo.CARS[0]["plate"], make=demo.CARS[0]["make"],
                model=demo.CARS[0]["model"], last_lat=0, last_lon=0),
            Car(id=11, owner_id=1, plate="REAL123", make="Toyota", model="Camry"),
            # Even a seed plate is insufficient when its car identity has changed.
            Car(id=12, owner_id=1, plate=demo.CARS[1]["plate"], make="Other", model="Vehicle"),
        ])
    return mongo_db


def position(car_id, timestamp=NOW, *, source=None):
    result = {"car_id": car_id, "ts": timestamp, "lat": 37.25, "lon": -121.8,
              "speed_kmh": 20, "heading": 90}
    if source is not None:
        result["source"] = source
    return result


def test_requires_explicit_opt_in_before_accessing_any_database(monkeypatch):
    def forbidden():
        pytest.fail("Missing opt-in must not access a database")

    monkeypatch.setattr(demo, "load_cars", forbidden)
    with pytest.raises(SystemExit) as exc:
        demo.main([])
    assert exc.value.code == 2


@pytest.mark.parametrize("args", [["--minutes", "0"], ["--minutes", "1441"], ["--offline", "-1"]])
def test_invalid_scope_and_duration_stop_before_database_access(monkeypatch, args):
    monkeypatch.setattr(demo, "load_cars", lambda: pytest.fail("Invalid arguments accessed a database"))
    with pytest.raises(SystemExit) as exc:
        demo.main(["--demo-only", *args])
    assert exc.value.code == 2


def test_scope_uses_seed_identity_instead_of_assuming_database_ids(feed_db):
    assert demo.load_cars() == [(10, 0, 0)]


def test_main_preserves_alerts_and_unrelated_positions_across_restarts(feed_db):
    feed_db.car_positions.insert_one(position(11))
    feed_db.alerts.insert_many([
        {"record_id": "seed-record", "car_id": 10, "timestamp": NOW - timedelta(days=30),
         "lat": 1, "lon": 2, "status": "resolved"},
        {"record_id": "real-record", "car_id": 11, "timestamp": NOW,
         "lat": 3, "lon": 4, "status": "new"},
    ])
    old_alerts = deepcopy(list(feed_db.alerts.find()))
    unrelated = deepcopy(list(feed_db.car_positions.find()))

    demo.main(["--demo-only", "--minutes", "1"])
    after_first = deepcopy(list(feed_db.car_positions.find()))
    assert len(after_first) == 13  # 12 new demo points, one unchanged real point.
    assert list(feed_db.car_positions.find({"car_id": 11})) == unrelated
    assert feed_db.car_positions.count_documents({"car_id": 12}) == 0
    assert feed_db.car_positions.count_documents({"car_id": 10, "source": demo.SOURCE}) == 12

    demo.main(["--demo-only", "--minutes", "2", "--keep-alerts"])
    assert list(feed_db.car_positions.find()) == after_first
    assert list(feed_db.alerts.find()) == old_alerts


@pytest.mark.parametrize("source", [None, "carla", "other-feed"])
def test_existing_real_or_legacy_history_is_untouched_even_when_offline(feed_db, source):
    repo = PositionRepo()
    repo.add(position(10, NOW - timedelta(days=30), source=source))
    # A newer marked demo point must not conceal an older real/untagged position.
    repo.add(position(10, NOW, source=demo.SOURCE))
    before = deepcopy(list(feed_db.car_positions.find()))
    assert demo.initialize_routes(repo, demo.load_cars(), NOW, 1, {10}) == {}
    assert list(feed_db.car_positions.find()) == before


def test_live_feed_hands_over_to_real_telemetry_without_changing_history(feed_db):
    repo = PositionRepo()
    state = demo.initialize_routes(repo, demo.load_cars(), NOW, 1, set())
    initial = deepcopy(list(feed_db.car_positions.find()))
    demo.tick_routes(repo, state, NOW + timedelta(seconds=5), set(), {10})
    assert feed_db.car_positions.count_documents({}) == 13
    assert list(feed_db.car_positions.find({"ts": {"$lte": NOW}})) == initial

    repo.add(position(10, NOW + timedelta(seconds=6), source="carla"))
    before_handover = deepcopy(list(feed_db.car_positions.find()))
    demo.tick_routes(repo, state, NOW + timedelta(seconds=10), set(), {10})
    assert state == {}
    assert list(feed_db.car_positions.find()) == before_handover
    assert demo.initialize_routes(repo, demo.load_cars(), NOW + timedelta(days=1), 120, set()) == {}
    assert list(feed_db.car_positions.find()) == before_handover


def test_real_telemetry_arriving_during_initial_history_stops_backfill(feed_db, monkeypatch):
    repo = PositionRepo()
    original_add = repo.add
    actual = position(10, NOW + timedelta(seconds=1), source="real-edge")
    inserted = False

    def edge_arrives_after_first_demo_point(point):
        nonlocal inserted
        result = original_add(point)
        if not inserted:
            inserted = True
            original_add(actual)
        return result

    monkeypatch.setattr(repo, "add", edge_arrives_after_first_demo_point)
    assert demo.initialize_routes(repo, demo.load_cars(), NOW, 1, set()) == {}
    assert feed_db.car_positions.count_documents({}) == 2
    stored = feed_db.car_positions.find_one({"source": "real-edge"})
    assert {key: value for key, value in stored.items() if key != "_id"} == actual


def test_offline_car_keeps_its_original_timestamps_on_restart(feed_db):
    repo = PositionRepo()
    state = demo.initialize_routes(repo, demo.load_cars(), NOW, 1, {10})
    before = deepcopy(list(feed_db.car_positions.find()))
    assert repo.latest(10)["ts"] == NOW - timedelta(minutes=10)
    demo.tick_routes(repo, state, NOW + timedelta(seconds=5), {10}, {10})
    restarted = demo.initialize_routes(repo, demo.load_cars(), NOW + timedelta(days=1), 120, {10})
    demo.tick_routes(repo, restarted, NOW + timedelta(days=1, seconds=5), {10}, {10})
    assert list(feed_db.car_positions.find()) == before


def test_quick_restart_or_duplicate_tick_never_replaces_a_demo_slot(feed_db):
    repo = PositionRepo()
    state = demo.initialize_routes(repo, demo.load_cars(), NOW, 1, set())
    demo.tick_routes(repo, state, NOW + timedelta(seconds=5, microseconds=1), set(), {10})
    before = deepcopy(list(feed_db.car_positions.find()))
    restarted = demo.initialize_routes(repo, demo.load_cars(), NOW + timedelta(seconds=6), 120, set())
    demo.tick_routes(repo, restarted, NOW + timedelta(seconds=9), set(), {10})
    assert list(feed_db.car_positions.find()) == before


def test_feed_stops_when_car_no_longer_matches_seed_identity(feed_db):
    repo = PositionRepo()
    state = demo.initialize_routes(repo, demo.load_cars(), NOW, 1, set())
    before = deepcopy(list(feed_db.car_positions.find()))
    with postgres.session_scope() as session:
        session.get(Car, 10).model = "A real replacement"
    demo.tick_routes(repo, state, NOW + timedelta(seconds=5), set(), {c[0] for c in demo.load_cars()})
    assert state == {}
    assert list(feed_db.car_positions.find()) == before


# --refresh: the original local-demo behaviour, opt-in and refused in production.

def test_refresh_is_refused_in_production_before_database_access(monkeypatch):
    monkeypatch.setattr(demo, "get_settings", lambda: type("S", (), {"APP_ENV": "production"})())
    monkeypatch.setattr(demo, "load_cars", lambda: pytest.fail("Refused --refresh accessed a database"))
    with pytest.raises(SystemExit) as exc:
        demo.main(["--demo-only", "--refresh"])
    assert exc.value.code == 2


def test_refresh_rewrites_demo_history_to_end_now(feed_db):
    repo = PositionRepo()
    stale = position(10, NOW - timedelta(days=1), source=demo.SOURCE)
    repo.add(stale)
    started = datetime.now(timezone.utc)

    demo.main(["--demo-only", "--refresh", "--minutes", "1", "--keep-alerts"])

    points = list(feed_db.car_positions.find({"car_id": 10}))
    assert len(points) == 12 and all(p["source"] == demo.SOURCE for p in points)
    assert min(p["ts"] for p in points) > NOW            # the day-old point is gone
    assert max(p["ts"] for p in points) >= started - timedelta(seconds=demo.STEP_SECONDS)


@pytest.mark.parametrize("source", [None, "carla"])
def test_refresh_leaves_cars_with_non_demo_positions_untouched(feed_db, source):
    PositionRepo().add(position(10, NOW - timedelta(days=1), source=source))
    before = deepcopy(list(feed_db.car_positions.find()))
    demo.main(["--demo-only", "--refresh", "--minutes", "1", "--keep-alerts"])
    assert list(feed_db.car_positions.find()) == before


def test_refresh_moves_only_development_seed_alerts_into_the_window(feed_db):
    seed_record = demo.load_seed_alerts()[0]["record_id"]
    old = NOW - timedelta(days=30)
    feed_db.alerts.insert_many([
        {"record_id": seed_record, "car_id": 10, "timestamp": old, "lat": 1, "lon": 2, "status": "new"},
        {"record_id": "real-record", "car_id": 11, "timestamp": old, "lat": 3, "lon": 4, "status": "new"},
    ])
    started = datetime.now(timezone.utc)

    demo.main(["--demo-only", "--refresh", "--minutes", "1"])

    seeded = feed_db.alerts.find_one({"record_id": seed_record})
    assert seeded["timestamp"] >= started - timedelta(minutes=1, seconds=5)
    assert (seeded["lat"], seeded["lon"]) != (1, 2)       # placed on car 10's fresh route
    real = feed_db.alerts.find_one({"record_id": "real-record"})
    assert (real["timestamp"], real["lat"], real["lon"]) == (old, 3, 4)
