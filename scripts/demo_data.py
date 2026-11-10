"""Dev helper: make the seeded data look like it is happening now, so the Map (F9), Tracking
(F10) and the dashboards have something to show.

The seed (`python -m app.db.seed`) writes 25 alerts with fixed timestamps and no car positions
at all, so a fresh database shows empty maps and "0 alerts today". This script fills in the
positions Rishi's CARLA simulator (C2) will eventually send, and moves the seeded alerts into
the last couple of hours.

Run it from the repo root after the seed:

    python scripts/demo_data.py                 # 2 hours of positions, alerts moved to the same window
    python scripts/demo_data.py --live          # then keep adding a position every 5 s
    python scripts/demo_data.py --offline 3     # leave car 3 behind, so its marker stays grey
    python scripts/demo_data.py --keep-alerts   # positions only, do not touch alert times

Running it again replaces the positions in the window it writes. Stop any --live run first, or
the two will write different routes over the same minutes.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import mongo, postgres                                    # noqa: E402
from app.db.repositories import AlertRepo, CarRepo, PositionRepo      # noqa: E402

CENTRE = (37.3352, -121.8811)          # downtown San Jose, where the seeded cars are
STEP_SECONDS = 5
SPEED_KMH = (0, 65)


def load_cars():
    with postgres.session_scope() as session:
        return [(c.id, c.last_lat or CENTRE[0], c.last_lon or CENTRE[1])
                for c in CarRepo(session).list_all()]


def drive(repo, car_id, start_lat, start_lon, until, points, heading):
    """Walk a car away from its start point, one position every STEP_SECONDS."""
    lat, lon = start_lat, start_lon
    for i in range(points, 0, -1):
        ts = until - timedelta(seconds=STEP_SECONDS * (i - 1))
        speed = random.uniform(*SPEED_KMH)
        metres = speed * 1000 / 3600 * STEP_SECONDS
        lat += metres * math.cos(math.radians(heading)) / 111_111
        lon += metres * math.sin(math.radians(heading)) / (111_111 * math.cos(math.radians(lat)))
        heading = (heading + random.uniform(-25, 25)) % 360
        repo.add_position(car_id, ts, round(lat, 6), round(lon, 6), round(speed, 1), round(heading, 1))
    return lat, lon, heading


def move_alerts(minutes):
    """Spread the seeded alerts over the last `minutes` and put each one on its car's route."""
    alerts = AlertRepo()
    positions = PositionRepo()
    found = alerts.search(page=1, page_size=100)["items"]
    if not found:
        return 0

    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes)
    found.sort(key=lambda a: a.get("timestamp") or start)
    step = (now - start) / max(len(found), 1)

    routes = {}
    for index, alert in enumerate(found):
        stamp = start + step * index
        car_id = alert["car_id"]
        if car_id not in routes:
            routes[car_id] = positions.track(car_id, start - timedelta(minutes=5), now)
        fields = {"timestamp": stamp}
        points = routes[car_id]
        if points:
            near = min(points, key=lambda p: abs((p["ts"] - stamp).total_seconds()))
            fields.update(lat=near["lat"], lon=near["lon"])
        alerts.update(alert["id"], fields)
    return len(found)


def main():
    parser = argparse.ArgumentParser(description="Fill in demo positions and refresh alert times")
    parser.add_argument("--minutes", type=int, default=120, help="how much history to write")
    parser.add_argument("--offline", type=int, nargs="*", default=[],
                        help="car ids to leave in the past, so their marker stays grey")
    parser.add_argument("--keep-alerts", action="store_true", help="do not move alert timestamps")
    parser.add_argument("--live", action="store_true",
                        help="after the history, keep adding a position every few seconds")
    args = parser.parse_args()

    cars = load_cars()
    if not cars:
        print("No cars in PostgreSQL. Run `python -m app.db.seed` from backend/ first.")
        return

    mongo.ensure_indexes()
    repo = PositionRepo()
    now = datetime.now(timezone.utc)
    state = {}
    for car_id, lat, lon in cars:
        stale = car_id in args.offline
        until = now - timedelta(minutes=10) if stale else now
        points = args.minutes * 60 // STEP_SECONDS
        # Replace, never add: two routes in the same window make the track zigzag between them.
        repo.delete_between(car_id, until - timedelta(seconds=STEP_SECONDS * points), until)
        state[car_id] = drive(repo, car_id, lat, lon, until, points, random.uniform(0, 360))
        print(f"car {car_id}: {points} positions up to {until:%H:%M:%S}"
              + (" (left offline)" if stale else ""))

    if not args.keep_alerts:
        print(f"moved {move_alerts(args.minutes)} alerts into the last {args.minutes} minutes")

    if not args.live:
        print("Done. Markers go grey after 90 s; re-run with --live to keep them moving.")
        return

    print("Live mode, press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(STEP_SECONDS)
            now = datetime.now(timezone.utc)
            for car_id, (lat, lon, heading) in list(state.items()):
                if car_id in args.offline:
                    continue
                state[car_id] = drive(repo, car_id, lat, lon, now, 1, heading)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
