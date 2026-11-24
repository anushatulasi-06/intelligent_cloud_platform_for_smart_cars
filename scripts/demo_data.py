"""Opt-in synthetic positions for the seeded demo cars (F9/F10).

Only cars whose plate, make and model match the development seed are eligible. History is
created only when a car has no positions. Generated points carry source="demo_data"; any
untagged or differently sourced position makes this feed leave that car alone, including
when a real edge starts reporting while the feed is running. Existing positions and alerts
are never deleted, updated or retimed.

Run it from the repo root after the seed:

    python scripts/demo_data.py --demo-only                 # initial history, once per empty car
    python scripts/demo_data.py --demo-only --live          # append a demo point every 5 s
    python scripts/demo_data.py --demo-only --offline 1 4   # no live points for cars 1 and 4

Rerunning preserves history and resumes only this feed's marked routes. Legacy unmarked demo
positions are intentionally treated as real data. --offline never backdates an existing point;
an already-online car becomes grey after its normal 90-second freshness window.

A car counts as online while its last position is less than 90 seconds old (DT-MV-1), so the
history alone turns every marker grey after a minute and a half. Use --live during a demo.
"""
import argparse
import math
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import mongo, postgres                                    # noqa: E402
from app.db.repositories import CarRepo, PositionRepo                 # noqa: E402
from app.db.seed import CARS                                         # noqa: E402

CENTRE = (37.3352, -121.8811)          # downtown San Jose, where the seeded cars are
STEP_SECONDS = 5
SPEED_KMH = (0, 65)
SOURCE = "demo_data"


class RealPositionPresent(RuntimeError):
    """A car has non-demo telemetry and must no longer receive synthetic positions."""


def load_cars():
    """Resolve seed natural keys rather than assuming database IDs are 1 through 4."""
    with postgres.session_scope() as session:
        repo = CarRepo(session)
        cars = []
        for seed in CARS:
            car = repo.get_by_plate(seed["plate"])
            if car is not None and (car.make, car.model) == (seed["make"], seed["model"]):
                cars.append((car.id, car.last_lat if car.last_lat is not None else CENTRE[0],
                             car.last_lon if car.last_lon is not None else CENTRE[1]))
        return cars


def _require_demo_history(repo, car_id):
    # Missing source covers both real edge telemetry and legacy unmarked demo data. Check the
    # entire car history, not just its newest point, so a later demo point cannot hide real data.
    if repo.find_one({"car_id": car_id, "source": {"$ne": SOURCE}}) is not None:
        raise RealPositionPresent


def _slot(now):
    """Stable five-second slots avoid duplicate points across quick or concurrent restarts."""
    return now.replace(microsecond=0) - timedelta(seconds=now.second % STEP_SECONDS)


def drive(repo, car_id, start_lat, start_lon, until, points, heading):
    """Walk a car away from its start point, one position every STEP_SECONDS."""
    lat, lon = start_lat, start_lon
    for i in range(points, 0, -1):
        _require_demo_history(repo, car_id)
        ts = until - timedelta(seconds=STEP_SECONDS * (i - 1))
        speed = random.uniform(*SPEED_KMH)
        metres = speed * 1000 / 3600 * STEP_SECONDS
        lat += metres * math.cos(math.radians(heading)) / 111_111
        lon += metres * math.sin(math.radians(heading)) / (111_111 * math.cos(math.radians(lat)))
        heading = (heading + random.uniform(-25, 25)) % 360
        point = {"_id": f"{SOURCE}:{car_id}:{ts.isoformat()}", "source": SOURCE,
                 "car_id": car_id, "ts": ts, "lat": round(lat, 6), "lon": round(lon, 6),
                 "speed_kmh": round(speed, 1), "heading": round(heading, 1)}
        try:
            repo.add(point)
        except DuplicateKeyError:
            # Another demo invocation already owns this slot. Never replace its coordinates.
            existing = repo.get(point["_id"])
            if existing is None or existing.get("source") != SOURCE:
                raise RealPositionPresent from None
            lat, lon, heading = existing["lat"], existing["lon"], existing["heading"]
    return lat, lon, heading


def initialize_routes(repo, cars, now, minutes, offline):
    state = {}
    for car_id, lat, lon in cars:
        try:
            _require_demo_history(repo, car_id)
            latest = repo.latest(car_id)
            if latest is None:
                until = _slot(now - timedelta(minutes=10) if car_id in offline else now)
                state[car_id] = drive(repo, car_id, lat, lon, until,
                                      minutes * 60 // STEP_SECONDS, random.uniform(0, 360))
                print(f"car {car_id}: created initial demo history")
            else:
                state[car_id] = latest["lat"], latest["lon"], latest.get("heading") or 0
                print(f"car {car_id}: preserved existing demo history")
        except RealPositionPresent:
            print(f"car {car_id}: existing non-demo history preserved; feed skipped")
    return state


def tick_routes(repo, state, now, offline, eligible_ids):
    for car_id, (lat, lon, heading) in list(state.items()):
        if car_id not in eligible_ids:
            state.pop(car_id)
            continue
        if car_id in offline:
            continue
        try:
            state[car_id] = drive(repo, car_id, lat, lon, _slot(now), 1, heading)
        except RealPositionPresent:
            state.pop(car_id)
            print(f"car {car_id}: non-demo telemetry arrived; demo feed stopped for this car")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Append marked demo positions without changing existing data")
    parser.add_argument("--demo-only", action="store_true",
                        help="explicitly opt in to synthetic telemetry for seed-matching cars only")
    parser.add_argument("--minutes", type=int, default=120, help="how much history to write")
    parser.add_argument("--offline", type=int, nargs="*", default=[],
                        help="car ids to leave in the past, so their marker stays grey")
    parser.add_argument("--keep-alerts", action="store_true",
                        help="compatibility option: all alerts are always preserved")
    parser.add_argument("--live", action="store_true",
                        help="after the history, keep adding a position every few seconds")
    args = parser.parse_args(argv)
    if not args.demo_only:
        parser.error("--demo-only is required; this feed is only for the development seed")
    if not 1 <= args.minutes <= 1440:
        parser.error("--minutes must be between 1 and 1440")
    if any(car_id < 1 for car_id in args.offline):
        parser.error("--offline car IDs must be positive")

    cars = load_cars()
    if not cars:
        print("No cars match the development seed's plate, make and model; nothing written.")
        return

    mongo.ensure_indexes()
    repo = PositionRepo()
    now = datetime.now(timezone.utc)
    state = initialize_routes(repo, cars, now, args.minutes, set(args.offline))
    print("All existing positions and alert timestamps preserved.")

    if not args.live:
        print("Done. Markers go grey after 90 s; re-run with --live to keep them moving.")
        return

    print("Live mode, press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(STEP_SECONDS)
            now = datetime.now(timezone.utc)
            tick_routes(repo, state, now, set(args.offline), {car[0] for car in load_cars()})
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
