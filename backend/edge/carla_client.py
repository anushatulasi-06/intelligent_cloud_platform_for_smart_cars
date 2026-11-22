"""Attach an existing CARLA vehicle to C2/C3 without changing the world or spawning a car."""
import argparse
import base64
import hashlib
import json
import logging
import math
import os
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Event, Lock
from uuid import NAMESPACE_URL, uuid5

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import SOUND_CATEGORIES
from app.services.edge_buffer import EdgeBuffer
from app.services.errors import ServiceError
from app.services.ingest_service import MAX_AUDIO_BYTES, validate_audio
from edge.audio_playback import SystemAudioPlayer

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceCredentials:
    id: int
    key: str = field(repr=False)


class CloudTransport:
    def __init__(self, base_url: str, client: httpx.Client | None = None, *, firmware="carla-edge-v1"):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=10)
        self.firmware = firmware

    def request(self, method, path, device, **kwargs):
        response = self.client.request(method, self.base_url + path,
                                       headers={"X-Device-Key": device.key}, **kwargs)
        response.raise_for_status()
        return response

    def heartbeat(self, device, position):
        response = self.request("POST", f"/devices/{device.id}/heartbeat", device,
                                json={"firmware": self.firmware, **position})
        return response.json()["commands"]

    def acknowledge(self, device, command_id, ok, detail):
        self.request("POST", f"/devices/{device.id}/commands/{command_id}/ack", device,
                     json={"ok": ok, "detail": detail})

    def upload(self, device, metadata, audio):
        fields = {key: str(value) for key, value in metadata.items()
                  if key != "content_type" and value is not None}
        response = self.request("POST", "/device-data", device, data=fields,
                                files={"audio_file": ("clip", audio, metadata["content_type"])})
        return response.status_code in (200, 202) and bool(response.json().get("record_id"))

    def frame(self, device, jpeg):
        self.request("POST", f"/devices/{device.id}/frame", device,
                     files={"frame": ("frame.jpg", jpeg, "image/jpeg")})

    def close(self):
        self.client.close()


def _write_private(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_scenarios(path: Path) -> dict[str, Path]:
    values = json.loads(path.read_text())
    if set(values) != set(SOUND_CATEGORIES) or not all(isinstance(value, str) for value in values.values()):
        raise ValueError("Scenario file must map all five sound categories to audio paths")
    return {category: (path.parent / value).resolve() for category, value in values.items()}


class CarlaEdgeClient:
    def __init__(self, vehicle, carla_map, cloud: CloudTransport, mic: DeviceCredentials, car_id: int,
                 buffer: EdgeBuffer, scenarios: dict[str, Path], *, camera: DeviceCredentials | None = None,
                 wifi_apply=None, traffic_manager_port: int = 8001, play_audio=None):
        self.vehicle, self.map, self.cloud = vehicle, carla_map, cloud
        self.mic, self.camera, self.car_id = mic, camera, car_id
        self.buffer, self.scenarios, self.wifi_apply = buffer, scenarios, wifi_apply
        self.traffic_manager_port = traffic_manager_port
        self.play_audio = play_audio
        # Capture here means forwarding explicit scenario clips, not recording a physical microphone.
        self.capture_enabled = True
        self.camera_capture_enabled = True
        self._results = OrderedDict()
        self._acks = {}
        self._frame, self._frame_lock = None, Lock()

    def telemetry(self):
        geo = self.map.transform_to_geolocation(self.vehicle.get_location())
        velocity = self.vehicle.get_velocity()
        return {"lat": geo.latitude, "lon": geo.longitude,
                **({"town": self.map.name.rsplit('/', 1)[-1]} if getattr(self.map, 'name', None) else {}),
                "speed_kmh": 3.6 * math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2),
                "heading": (self.vehicle.get_transform().rotation.yaw + 90) % 360}

    def set_frame(self, jpeg: bytes):
        with self._frame_lock:
            self._frame = jpeg

    def _wifi(self, device, payload):
        wifi = payload.get("wifi")
        if not wifi:
            return True, None  # Initial sync for a device without Wi-Fi settings.
        settings = dict(wifi)
        if settings.get("password_enc"):
            cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(device.key.encode()).digest()))
            settings["password"] = cipher.decrypt(settings.pop("password_enc").encode()).decode()
        # Persist only the encrypted envelope, never the decoded password.
        _write_private(self.buffer.directory / "wifi" / f"{device.id}.json", wifi)
        if self.wifi_apply is None:
            return False, "Settings saved; no OS Wi-Fi apply hook is configured"
        applied = self.wifi_apply(device.id, settings) is True
        return applied, None if applied else "Wi-Fi apply hook did not confirm success"

    def _execute(self, device, command, position):
        name, payload = command["command"], command.get("payload") or {}
        if name in ("start_route", "stop_route"):
            self.vehicle.set_autopilot(name == "start_route", self.traffic_manager_port)
            if name == "stop_route":
                control = self.vehicle.get_control()
                control.throttle, control.brake = 0.0, 1.0
                self.vehicle.apply_control(control)
            return True, None
        if name in ("start_capture", "stop_capture"):
            if self.camera and device.id == self.camera.id:
                self.camera_capture_enabled = name == "start_capture"
            else:
                self.capture_enabled = name == "start_capture"
            return True, None
        if name == "sync_config":
            return self._wifi(device, payload)
        if name == "reboot":
            return False, "Remote reboot is not supported by this client"
        if name != "simulate_event":
            return False, "Unsupported command"
        if not self.capture_enabled:
            return False, "Scenario capture is stopped; send start_capture first"
        category = payload.get("scenario")
        source = self.scenarios.get(category)
        if source is None or source.suffix.lower() not in (".wav", ".mp3") or not source.is_file():
            return False, "Configure a local WAV or MP3 clip for this scenario"
        with source.open("rb") as stream:
            audio = stream.read(MAX_AUDIO_BYTES + 1)
        content_type = "audio/mpeg" if source.suffix.lower() == ".mp3" else "audio/wav"
        validate_audio(audio, content_type)
        # Stable across ambiguous ACKs, upload retries, and edge-process restarts.
        clip_id = str(uuid5(NAMESPACE_URL, f"{self.cloud.base_url}/devices/{device.id}/commands/{command['command_id']}"))
        self.buffer.enqueue(audio, {"clip_id": clip_id, "device_id": self.mic.id, "car_id": self.car_id,
                                   "content_type": content_type, "trigger": category,
                                   "captured_at": datetime.now(timezone.utc).isoformat(),
                                   "lat": position["lat"], "lon": position["lon"]})
        detail = None
        if self.play_audio is not None:
            try:
                started = self.play_audio(source) is True
            except Exception as exc:
                log.warning("Edge audio playback unavailable (%s)", type(exc).__name__)
                started = False
            detail = "Clip buffered; edge audio player started" if started else "Clip buffered; use browser preview for audio"
        return True, detail  # Captured durably; playback never prevents the upload.

    def _retry_acks(self):
        # A backlog of slow ACKs must not delay the next liveness heartbeat.
        for (device_id, command_id), (device, ok, detail) in list(self._acks.items())[:2]:
            try:
                self.cloud.acknowledge(device, command_id, ok, detail)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (404, 409):
                    continue
            except httpx.HTTPError:
                continue
            self._acks.pop((device_id, command_id))

    def step(self):
        position = self.telemetry()
        for device in (self.mic, self.camera):
            if device is None:
                continue
            for command in self.cloud.heartbeat(device, position):
                identity = (device.id, command["command_id"])
                if identity not in self._results:
                    try:
                        result = self._execute(device, command, position)
                    except (OSError, ValueError, RuntimeError, BufferError, InvalidToken, ServiceError):
                        result = (False, "Command failed; check local scenario and configuration settings")
                    self._results[identity] = result
                    if len(self._results) > 256:
                        self._results.popitem(last=False)
                self._acks[identity] = (device, *self._results[identity])
        self._retry_acks()

        def send(metadata, audio):
            try:
                return self.cloud.upload(self.mic, metadata, audio)
            except (httpx.HTTPError, ValueError):
                return False
        # Recovery of a full FIFO can take minutes; keep heartbeats between clips.
        self.buffer.flush(send, limit=1)
        if self.camera:
            with self._frame_lock:
                frame, self._frame = self._frame, None
            if frame is not None and self.camera_capture_enabled:
                self.cloud.frame(self.camera, frame)


def _jpeg(image):
    from PIL import Image
    frame = Image.frombytes("RGBA", (image.width, image.height), image.raw_data, "raw", "BGRA").convert("RGB")
    stream = BytesIO()
    frame.save(stream, format="JPEG", quality=80)
    return stream.getvalue()


def _device_from_env(prefix):
    try:
        device = DeviceCredentials(int(os.environ[f"{prefix}_ID"]), os.environ[f"{prefix}_KEY"])
        if device.id < 1 or not device.key:
            raise ValueError
        return device
    except (KeyError, ValueError):
        raise ValueError(f"Set {prefix}_ID and {prefix}_KEY for a registered device") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor-id", type=int, required=True, help="Existing CARLA vehicle actor id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--traffic-manager-port", type=int, default=8001,
                        help="CARLA Traffic Manager port (8001 avoids the API's port 8000)")
    parser.add_argument("--camera", action="store_true", help="Attach one owned RGB camera sensor")
    parser.add_argument("--scenarios", type=Path, default=Path(__file__).with_name("edge_scenarios.json"))
    parser.add_argument("--buffer-dir", type=Path, default=Path(".local_edge"))
    parser.add_argument("--no-audio-playback", action="store_true", help="Use browser preview instead of an installed local sound player")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        mic = _device_from_env("EDGE_DEVICE")
        camera = _device_from_env("EDGE_CAMERA_DEVICE") if args.camera else None
        car_id = int(os.environ["EDGE_CAR_ID"])
        if car_id < 1:
            raise ValueError("EDGE_CAR_ID must be positive")
        scenarios = load_scenarios(args.scenarios)
    except (KeyError, ValueError, OSError) as exc:
        parser.error(str(exc))
    try:
        import carla
        if args.camera:
            from PIL import Image  # noqa: F401 — verify optional dependency before spawning a sensor
    except ImportError:
        parser.error("Install a CARLA client matching your simulator; --camera also needs Pillow (see edge/README.md)")
    connection = carla.Client(args.host, args.port)
    connection.set_timeout(10)
    world = connection.get_world()
    vehicle = world.get_actor(args.actor_id)
    if vehicle is None or not vehicle.type_id.startswith("vehicle."):
        parser.error("--actor-id must identify an existing CARLA vehicle")
    connection.get_trafficmanager(args.traffic_manager_port)
    cloud = CloudTransport(os.environ.get("EDGE_API_URL", "http://localhost:8000"))
    audio_player = SystemAudioPlayer(enabled=not args.no_audio_playback)
    log.info("Scenario sound output: %s", audio_player.description)
    adapter = CarlaEdgeClient(vehicle, world.get_map(), cloud, mic, car_id,
                              EdgeBuffer(args.buffer_dir), scenarios, camera=camera,
                              traffic_manager_port=args.traffic_manager_port, play_audio=audio_player)
    sensor, stop = None, Event()
    try:
        if args.camera:
            blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
            for key, value in {"image_size_x": "640", "image_size_y": "360", "sensor_tick": "2.0"}.items():
                blueprint.set_attribute(key, value)
            sensor = world.spawn_actor(blueprint, carla.Transform(carla.Location(x=1.5, z=2.4)), attach_to=vehicle)
            sensor.listen(lambda image: adapter.set_frame(_jpeg(image)))
        while not stop.is_set():
            try:
                adapter.step()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 404):
                    raise SystemExit("Device rejected or deleted; check the registered ids and keys") from None
                log.warning("Cloud request failed (%s); buffered audio is retained", exc.response.status_code)
            except (httpx.HTTPError, OSError, RuntimeError):
                log.warning("Edge connection unavailable; buffered audio is retained")
            stop.wait(2)
    except KeyboardInterrupt:
        stop.set()
    finally:
        try:
            if sensor is not None:
                try:
                    sensor.stop()
                finally:
                    sensor.destroy()
        finally:
            try:
                audio_player.close()
            finally:
                cloud.close()


if __name__ == "__main__":
    main()
