#!/usr/bin/env python3
"""Record one CARLA scenario per sound class, for replay by the edge client (edge.replay_client).

Scenarios (one per C4 sound class):
  emergency      An ambulance with siren lights comes toward the car; the car slows and stops to give way.
  in_vehicle     A parked car blocks the lane ahead; the car does not brake and crashes into it.
  human          A pedestrian crosses in front of the car; the car brakes and stops for them.
  traffic        Busy traffic around the car; the car brakes suddenly (as if cut off) and moves on.
  environmental  The car drives through a heavy thunderstorm (rain, wind, wet roads).

Each run writes to <out>/<scenario>/:
  frames/000001.jpg ...  RGB camera frames, one every --interval seconds
  telemetry.csv          one row per frame: time, lat, lon, speed, heading, x, y, event
  scenario.json          the scenario, its sound class and the event times (when to send the clip)

Needs a running CARLA 0.9.16 server and the matching client (infra/carla/requirements-client.txt);
run it on the GPU machine, not inside the app. Copy the output into backend/edge/carla_recordings/.

Examples:
  python record_scenarios.py --scenario human
  python record_scenarios.py --scenario all --town Town03_Opt --interval 1.0
Only actors this script creates are removed. The weather is restored after "environmental".
"""
import argparse
import csv
import json
import math
import queue
import random
import time
from pathlib import Path

import carla

SCENARIOS = {
    "emergency": {"sound_class": "emergency", "seconds": 35, "clip_hint": "siren, ambulance"},
    "in_vehicle": {"sound_class": "in_vehicle", "seconds": 20, "clip_hint": "crash, glass breaking"},
    "human": {"sound_class": "human", "seconds": 25, "clip_hint": "scream, shout"},
    "traffic": {"sound_class": "traffic", "seconds": 35, "clip_hint": "car horn"},
    "environmental": {"sound_class": "environmental", "seconds": 35, "clip_hint": "rain, thunder, wind"},
}


def straight_spawn(cmap, spawns, ahead_m, rng):
    """A spawn point with at least `ahead_m` metres of road ahead and no junction."""
    candidates = spawns[:]
    rng.shuffle(candidates)
    for sp in candidates:
        wp = cmap.get_waypoint(sp.location)
        nxt = wp.next(ahead_m)
        if not nxt or wp.is_junction:
            continue
        end = nxt[0]
        if end.road_id == wp.road_id and end.lane_id == wp.lane_id and not end.is_junction:
            return sp, wp
    return spawns[0], cmap.get_waypoint(spawns[0].location)


def lifted(transform, z=0.5):
    loc = transform.location
    return carla.Transform(carla.Location(loc.x, loc.y, loc.z + z), transform.rotation)


def relative(ego, location):
    """(ahead, sideways) distance of `location` from the ego, in metres."""
    tr = ego.get_transform()
    d = location - tr.location
    f, r = tr.get_forward_vector(), tr.get_right_vector()
    return d.x * f.x + d.y * f.y, d.x * r.x + d.y * r.y


def speed_kmh(actor):
    v = actor.get_velocity()
    return 3.6 * math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)


def run(client, world, name, args, rng):
    cfg = SCENARIOS[name]
    seconds = args.seconds or cfg["seconds"]
    out = args.out / name
    (out / "frames").mkdir(parents=True, exist_ok=True)
    cmap, lib = world.get_map(), world.get_blueprint_library()
    tm = client.get_trafficmanager(args.tm_port)
    actors, events, rows = [], [], []
    original_weather = world.get_weather()
    state = {"collided": False, "braking": False, "stopped_at": None}

    def event(t, label):
        if not any(e["label"] == label for e in events):
            events.append({"t_s": round(t, 2), "label": label, "sound_class": cfg["sound_class"]})
            print(f"  event at {t:.1f}s: {label}")

    def spawn(bp_id_or_filter, transform):
        found = lib.filter(bp_id_or_filter)
        if not found:
            return None
        bp = rng.choice(found)
        if bp.has_attribute("role_name"):
            bp.set_attribute("role_name", "scenario")
        a = world.try_spawn_actor(bp, lifted(transform))
        if a:
            actors.append(a)
        return a

    try:
        sp, wp = straight_spawn(cmap, cmap.get_spawn_points(), 60, rng)
        ego_bp = lib.find("vehicle.tesla.model3")
        ego_bp.set_attribute("role_name", "hero")
        ego = world.try_spawn_actor(ego_bp, lifted(sp))
        if ego is None:
            raise RuntimeError("Could not spawn the ego car; try another --seed")
        actors.append(ego)

        cam_bp = lib.find("sensor.camera.rgb")
        cam_bp.set_attribute("image_size_x", str(args.width))
        cam_bp.set_attribute("image_size_y", str(args.height))
        cam_bp.set_attribute("fov", "90")
        cam_bp.set_attribute("sensor_tick", str(args.interval))
        cam = world.spawn_actor(cam_bp, carla.Transform(carla.Location(x=1.5, z=1.6)), attach_to=ego)
        actors.append(cam)
        images = queue.Queue()
        cam.listen(images.put)

        col = world.spawn_actor(lib.find("sensor.other.collision"), carla.Transform(), attach_to=ego)
        actors.append(col)
        col.listen(lambda e: state.__setitem__("collided", True))

        # ---- scenario setup -------------------------------------------------
        manual = name in ("in_vehicle", "human")
        other = None
        if name == "emergency":
            ahead = wp.next(70)[0]
            lane = ahead.get_left_lane()
            if lane and lane.lane_type == carla.LaneType.Driving and lane.lane_id * ahead.lane_id < 0:
                ahead = lane  # opposite lane, driving toward us
            other = spawn("vehicle.ford.ambulance", ahead.transform) or spawn("vehicle.*firetruck*", ahead.transform) \
                or spawn("vehicle.*police*", ahead.transform)
            if other:
                lights = carla.VehicleLightState.Special1 | carla.VehicleLightState.LowBeam
                other.set_light_state(carla.VehicleLightState(lights))
                other.set_autopilot(True, args.tm_port)
                tm.ignore_lights_percentage(other, 100)
                tm.vehicle_percentage_speed_difference(other, -40)  # faster than the limit
        elif name == "in_vehicle":
            other = spawn("vehicle.audi.a2", wp.next(40)[0].transform) or spawn("vehicle.*", wp.next(40)[0].transform)
            if other:
                other.apply_control(carla.VehicleControl(hand_brake=True))
        elif name == "human":
            ahead = wp.next(35)[0]
            side = ahead.transform.get_right_vector()
            off = ahead.lane_width / 2 + 2.0
            loc = ahead.transform.location + carla.Location(side.x * off, side.y * off, 1.0)
            other = spawn("walker.pedestrian.*", carla.Transform(loc, ahead.transform.rotation))
            state["walk_dir"] = carla.Vector3D(-side.x, -side.y, 0.0)
        elif name == "traffic":
            free = cmap.get_spawn_points()
            rng.shuffle(free)
            for t in free[:args.traffic]:
                if t.location.distance(sp.location) > 10:
                    car = spawn("vehicle.*", t)
                    if car:
                        car.set_autopilot(True, args.tm_port)
        elif name == "environmental":
            storm = carla.WeatherParameters(
                cloudiness=100.0, precipitation=100.0, precipitation_deposits=90.0, wind_intensity=100.0,
                fog_density=20.0, wetness=100.0, sun_altitude_angle=15.0)
            world.set_weather(storm)

        if not manual:
            ego.set_autopilot(True, args.tm_port)
            if name == "emergency":
                tm.vehicle_percentage_speed_difference(ego, 20)

        print(f"Recording '{name}' ({cfg['sound_class']}) for {seconds}s on {cmap.name} -> {out}")
        start, n = time.monotonic(), 0
        while time.monotonic() - start < seconds:
            world.wait_for_tick(30)
            t = time.monotonic() - start
            label = ""

            # ---- scenario logic each tick -----------------------------------
            if name == "emergency":
                near = other is not None and other.get_location().distance(ego.get_location()) < 35
                if (near or t >= 15) and not state["braking"]:
                    state["braking"] = True
                    state["stopped_at"] = t
                    ego.set_autopilot(False, args.tm_port)
                    event(t, "emergency vehicle approaching; car gives way")
                if state["braking"]:
                    ego.apply_control(carla.VehicleControl(throttle=0.0, brake=0.6, steer=0.05))
                    if t - state["stopped_at"] > 10:
                        state["braking"] = False
                        state["stopped_at"] = 1e9
                        ego.set_autopilot(True, args.tm_port)
            elif name == "in_vehicle":
                if state["collided"]:
                    event(t, "collision with parked car")
                    ego.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
                else:
                    ego.apply_control(carla.VehicleControl(throttle=0.55, steer=0.0))
            elif name == "human":
                if other is not None:
                    ahead_m, side_m = relative(ego, other.get_location())
                    if ahead_m < 28:
                        other.apply_control(carla.WalkerControl(direction=state["walk_dir"], speed=1.6))
                    if 0 < ahead_m < 16 and abs(side_m) < 3.0:
                        state["braking"] = True
                        event(t, "pedestrian crossing; car brakes")
                ego.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0) if state["braking"]
                                  else carla.VehicleControl(throttle=0.4, steer=0.0))
            elif name == "traffic":
                stop_t = args.event_at or 15
                if stop_t <= t < stop_t + 3:
                    if not state["braking"]:
                        state["braking"] = True
                        ego.set_autopilot(False, args.tm_port)
                        event(t, "sudden stop in traffic; horn")
                    ego.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
                elif state["braking"] and t >= stop_t + 3:
                    state["braking"] = False
                    ego.set_autopilot(True, args.tm_port)
            elif name == "environmental":
                if t >= (args.event_at or 5):
                    event(t, "heavy storm: rain, wind, thunder")

            if events and t - events[-1]["t_s"] < 3:
                label = events[-1]["label"]

            while not images.empty():
                img = images.get()
                n += 1
                img.save_to_disk(str(out / "frames" / f"{n:06d}.jpg"))
                tr = ego.get_transform()
                geo = cmap.transform_to_geolocation(tr.location)
                rows.append({
                    "t_s": round(t, 2), "frame": img.frame, "file": f"frames/{n:06d}.jpg",
                    "lat": geo.latitude, "lon": geo.longitude, "speed_kmh": round(speed_kmh(ego), 2),
                    "heading": round((tr.rotation.yaw + 90) % 360, 1),
                    "x": round(tr.location.x, 2), "y": round(tr.location.y, 2), "event": label,
                })
    finally:
        for a in reversed(actors):
            try:
                if a.type_id.startswith("sensor."):
                    a.stop()
                a.destroy()
            except Exception:
                pass
        if name == "environmental":
            world.set_weather(original_weather)

    with open(out / "telemetry.csv", "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    summary = {
        "scenario": name, "sound_class": cfg["sound_class"], "clip_hint": cfg["clip_hint"],
        "town": cmap.name, "seconds": seconds, "interval_s": args.interval, "frames": len(rows),
        "camera": f"{args.width}x{args.height}", "events": events,
        "note": "Send one clip of this sound class at each event time when replaying.",
    }
    (out / "scenario.json").write_text(json.dumps(summary, indent=2))
    print(f"Done '{name}': {len(rows)} frames, events={[e['t_s'] for e in events]}")
    if not events:
        print("  Warning: the event did not happen in this run; rerun with another --seed.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2000)
    # 8001, not 8000: the API listens on 8000 when it runs on the same machine (same as the edge clients).
    ap.add_argument("--tm-port", type=int, default=8001)
    ap.add_argument("--town", help="Load this map first, e.g. Town03_Opt")
    ap.add_argument("--seconds", type=float, help="Override the scenario's default length")
    ap.add_argument("--event-at", type=float, help="Event time for traffic/environmental (default 15 / 5 s)")
    ap.add_argument("--interval", type=float, default=0.5, help="Seconds between saved frames")
    ap.add_argument("--traffic", type=int, default=40, help="Number of other cars in 'traffic'")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--seed", type=int, default=7, help="Change to get a different start point")
    ap.add_argument("--out", type=Path, default=Path("runs"))
    args = ap.parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(60)
    world = client.load_world(args.town) if args.town else client.get_world()
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for i, name in enumerate(names):
        run(client, world, name, args, random.Random(args.seed + i))


if __name__ == "__main__":
    main()
