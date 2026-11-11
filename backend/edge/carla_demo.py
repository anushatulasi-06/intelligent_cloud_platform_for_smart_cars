"""Run one owned real CARLA vehicle and optional camera through the C2/C3 API.

This runner connects to an already running, advancing CARLA server. Wi-Fi and
reboot commands operate on a simulated device, not the host operating system.
"""
import argparse
from contextlib import AbstractContextManager
import logging
import os
from pathlib import Path
import signal
from threading import Event
import time

import httpx

from app.services.edge_buffer import EdgeBuffer
from edge.audio_playback import SystemAudioPlayer
from edge.carla_client import (CarlaEdgeClient, CloudTransport, _device_from_env, _jpeg,
                               _write_private, load_scenarios)

log = logging.getLogger(__name__)


class OwnedCarlaVehicle(AbstractContextManager):
    """Own only the vehicle/sensor created here, including partial setup failures."""
    def __init__(self, world, carla, *, traffic_manager_port=8001):
        self.world, self.carla = world, carla
        self.traffic_manager_port = traffic_manager_port
        self.vehicle, self.camera = None, None

    def __enter__(self):
        choices = [blueprint for blueprint in self.world.get_blueprint_library().filter("vehicle.*")
                   if blueprint.has_attribute("number_of_wheels")
                   and blueprint.get_attribute("number_of_wheels").as_int() == 4]
        choices.sort(key=lambda blueprint: (blueprint.id != "vehicle.tesla.model3", blueprint.id))
        if not choices:
            raise RuntimeError("CARLA has no four-wheel vehicle blueprint")
        blueprint = choices[0]
        if blueprint.has_attribute("role_name"):
            blueprint.set_attribute("role_name", "rishi-c2-c3-demo")
        for spawn in self.world.get_map().get_spawn_points():
            self.vehicle = self.world.try_spawn_actor(blueprint, spawn)
            if self.vehicle is not None:
                return self
        raise RuntimeError("CARLA has no unoccupied vehicle spawn point")

    def attach_camera(self, callback, *, width=320, height=180):
        blueprint = self.world.get_blueprint_library().find("sensor.camera.rgb")
        # Capture each rendered frame. A 2-second simulation-time sensor interval
        # becomes much longer in wall time on a slow GPU. Uploads remain bounded
        # by the edge's 2-second heartbeat loop and retain only the latest image.
        for key, value in {"image_size_x": str(width), "image_size_y": str(height),
                           "sensor_tick": "0.0", "fov": "90"}.items():
            blueprint.set_attribute(key, value)
        self.camera = self.world.spawn_actor(blueprint,
            self.carla.Transform(self.carla.Location(x=1.5, z=2.4)), attach_to=self.vehicle)
        self.camera.listen(callback)
        return self.camera

    def __exit__(self, exc_type, exc, traceback):
        if self.camera is not None:
            try:
                self.camera.stop()
            except Exception:
                log.warning("Could not stop owned CARLA camera")
        if self.vehicle is not None:
            try:
                self.vehicle.set_autopilot(False, self.traffic_manager_port)
            except Exception:
                pass
        for actor in (self.camera, self.vehicle):
            if actor is not None:
                try:
                    actor.destroy()
                except Exception:
                    log.warning("Could not destroy owned CARLA actor %s", actor.id)
        return False


class CarlaDemoClient(CarlaEdgeClient):
    """Real CARLA telemetry/camera with explicitly virtual device configuration."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, wifi_apply=lambda device_id, settings: True, **kwargs)

    def _execute(self, device, command, position):
        if command["command"] == "reboot":
            super()._execute(device, {"command": "stop_route"}, position)
            self.capture_enabled = self.camera_capture_enabled = True
            return True, "Simulated device restarted; host OS unchanged"
        ok, detail = super()._execute(device, command, position)
        if ok and command["command"] == "sync_config":
            detail = "Simulated Wi-Fi configured; host network unchanged"
        return ok, detail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--traffic-manager-port", type=int, default=8001)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--camera", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--camera-width", "--width", dest="width", type=int, default=320)
    parser.add_argument("--camera-height", "--height", dest="height", type=int, default=180)
    parser.add_argument("--heartbeat-seconds", type=float, default=2)
    parser.add_argument("--scenarios", type=Path, default=Path(__file__).with_name("edge_scenarios.json"))
    parser.add_argument("--buffer-dir", type=Path, default=Path(".local_edge/carla-demo"))
    parser.add_argument("--no-edge-audio", "--no-audio-playback", dest="no_audio_playback", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if not 0.2 <= args.heartbeat_seconds <= 30 or args.timeout <= 0:
            raise ValueError("Use a positive timeout and a heartbeat between 0.2 and 30 seconds")
        if not 32 <= args.width <= 1920 or not 32 <= args.height <= 1080:
            raise ValueError("Camera dimensions must be within 32x32 and 1920x1080")
        mic = _device_from_env("EDGE_DEVICE")
        camera = _device_from_env("EDGE_CAMERA_DEVICE") if args.camera else None
        if camera and camera.id == mic.id:
            raise ValueError("Microphone and camera must be different registered devices")
        car_id = int(os.environ["EDGE_CAR_ID"])
        if car_id < 1:
            raise ValueError("EDGE_CAR_ID must be positive")
        scenarios = load_scenarios(args.scenarios)
        if any(not path.is_file() for path in scenarios.values()):
            raise ValueError("A configured scenario recording is missing")
        import carla
    except (KeyError, ImportError, ValueError, OSError) as exc:
        parser.error(str(exc))
    connection = carla.Client(args.host, args.port)
    connection.set_timeout(args.timeout)
    log.info("Connected to actual CARLA server %s", connection.get_server_version())
    world = connection.get_world()
    before = world.get_snapshot().frame
    snapshot = world.wait_for_tick(args.timeout)
    if snapshot is None or snapshot.frame <= before:
        raise SystemExit("CARLA world is not advancing; retain its existing clock owner")
    connection.get_trafficmanager(args.traffic_manager_port)
    cloud = CloudTransport(os.environ.get("EDGE_API_URL", "http://localhost:8000"), firmware="carla-demo-v1")
    player = SystemAudioPlayer(enabled=not args.no_audio_playback)
    stop = Event()
    previous_handlers = {kind: signal.getsignal(kind) for kind in (signal.SIGINT, signal.SIGTERM)}
    for kind in previous_handlers:
        signal.signal(kind, lambda signum, frame: stop.set())
    state_file = args.buffer_dir / "carla-demo-state.json"
    state = None
    try:
        with OwnedCarlaVehicle(world, carla, traffic_manager_port=args.traffic_manager_port) as owned:
            adapter = CarlaDemoClient(owned.vehicle, world.get_map(), cloud, mic, car_id,
                                      EdgeBuffer(args.buffer_dir), scenarios, camera=camera,
                                      traffic_manager_port=args.traffic_manager_port, play_audio=player)
            if camera:
                def capture(image):
                    try:
                        adapter.set_frame(_jpeg(image))
                    except Exception:
                        log.warning("A CARLA camera frame could not be encoded")
                owned.attach_camera(capture, width=args.width, height=args.height)
            state = {"running": True, "server_host": args.host, "server_port": args.port,
                     "car_id": car_id, "vehicle_id": owned.vehicle.id,
                     "camera_actor_id": owned.camera.id if owned.camera else None,
                     "town": world.get_map().name, "firmware": "carla-demo-v1"}
            _write_private(state_file, state)
            log.info("Owned CARLA vehicle %s and camera %s on %s; waiting for UI commands",
                     owned.vehicle.id, state["camera_actor_id"], state["town"])
            log.info("Device Wi-Fi/reboot are simulated; sound output: %s", player.description)
            while not stop.is_set():
                started = time.monotonic()
                try:
                    adapter.step()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (401, 403, 404):
                        raise SystemExit("Device rejected or deleted; check ids, keys and car assignment") from None
                    log.warning("Cloud request failed (%s); buffered audio retained", exc.response.status_code)
                except (httpx.HTTPError, OSError, RuntimeError):
                    log.warning("Edge connection unavailable; buffered audio retained")
                stop.wait(max(0, args.heartbeat_seconds - (time.monotonic() - started)))
    finally:
        try:
            if state is not None:
                _write_private(state_file, {**state, "running": False})
        finally:
            try:
                player.close()
            finally:
                cloud.close()
                for kind, handler in previous_handlers.items():
                    signal.signal(kind, handler)


if __name__ == "__main__":
    main()
