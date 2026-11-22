"""Local edge demo: virtual vehicle, illustrated camera and real C2/C3 HTTP requests.

This is an explicit alternative to CARLA for component demonstrations. It does not
pretend to measure sound recognition accuracy or apply the host's Wi-Fi settings.
"""
import argparse
import logging
import math
import os
import time
import wave
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.config import SOUND_CATEGORIES
from app.services.edge_buffer import EdgeBuffer
from edge.carla_client import CarlaEdgeClient, CloudTransport, _device_from_env, load_scenarios
from edge.audio_playback import SystemAudioPlayer

log = logging.getLogger(__name__)
BUNDLED_SCENARIOS = Path(__file__).with_name("demo_clips") / "scenarios.json"


def make_synthetic_scenarios(directory: Path) -> dict[str, Path]:
    """Create distinct test tones, never real-world or model evaluation samples.

    A scenario selects the upload's trigger metadata. The classifier determines
    its own prediction, which may differ from the requested scenario.
    """
    directory.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    t = np.arange(sample_rate * 3, dtype=np.float64) / sample_rate
    waves = {
        "emergency": np.sin(2 * np.pi * (650 * t - 150 / (2 * np.pi) * np.cos(2 * np.pi * t))),
        "in_vehicle": np.sin(2 * np.pi * 110 * t) + 0.3 * np.sin(2 * np.pi * 220 * t),
        "human": np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)),
        "traffic": np.sin(2 * np.pi * 440 * t) * (np.sin(2 * np.pi * 1.4 * t) > 0),
        "environmental": np.random.default_rng(281).normal(0, 0.3, len(t)),
    }
    scenarios = {}
    for category, samples in waves.items():
        path = directory / f"synthetic-{category}.wav"
        # Do not overwrite files supplied by a user under the generated directory.
        if not path.exists():
            samples = samples / max(1.0, float(np.abs(samples).max()))
            pcm = (samples * 12000).astype("<i2")
            with wave.open(str(path), "wb") as stream:
                stream.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
                stream.writeframes(pcm.tobytes())
        scenarios[category] = path
    (directory / "README.txt").write_text(
        "SYNTHETIC TEST AUDIO. Generated tones exercise ingestion and inference only.\n"
        "These files are not real sirens, voices, traffic or model accuracy evidence.\n"
        "The scenario trigger is not the classifier's predicted label.\n"
        "Use --scenarios with actual recordings for a sound classification demo.\n"
    )
    return scenarios


class DemoVehicle:
    """Small CARLA-compatible actor moving around a fixed San Jose oval."""
    type_id = "vehicle.local-demo"
    radius_x, radius_y, speed_mps = 220.0, 140.0, 9.0

    def __init__(self):
        self.phase = 0.0
        self.running = False
        self.control = SimpleNamespace(throttle=0.0, brake=1.0)
        self.distance_m = 0.0

    def advance(self, seconds):
        if self.running:
            # arc-length-based angular velocity keeps the reported speed coherent
            scale = math.hypot(self.radius_x * math.sin(self.phase), self.radius_y * math.cos(self.phase))
            self.phase = (self.phase + self.speed_mps * seconds / scale) % (2 * math.pi)
            self.distance_m += self.speed_mps * seconds

    def get_location(self):
        return SimpleNamespace(x=self.radius_x * math.cos(self.phase),
                               y=self.radius_y * math.sin(self.phase), z=0.0)

    def get_velocity(self):
        dx = -self.radius_x * math.sin(self.phase)
        dy = self.radius_y * math.cos(self.phase)
        scale = math.hypot(dx, dy)
        speed = self.speed_mps if self.running else 0.0
        return SimpleNamespace(x=dx / scale * speed, y=dy / scale * speed, z=0.0)

    def get_transform(self):
        yaw = math.degrees(math.atan2(self.radius_y * math.cos(self.phase),
                                      -self.radius_x * math.sin(self.phase)))
        return SimpleNamespace(rotation=SimpleNamespace(yaw=yaw))

    def set_autopilot(self, enabled, port):
        self.running = enabled
        self.control.throttle, self.control.brake = (0.4, 0.0) if enabled else (0.0, 1.0)

    def get_control(self):
        return self.control

    def apply_control(self, control):
        self.control = control


class DemoMap:
    name = "Local edge demo - San Jose"

    def transform_to_geolocation(self, location):
        latitude = 37.3352 + location.y / 111320
        longitude = -121.8811 + location.x / (111320 * math.cos(math.radians(37.3352)))
        return SimpleNamespace(latitude=latitude, longitude=longitude)


def _font(size):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def render_demo_frame(vehicle: DemoVehicle, *, car_id: int, synthetic_audio: bool) -> bytes:
    """Render an unmistakably illustrated demo camera, with live telemetry."""
    width, height = 960, 540
    image = Image.new("RGB", (width, height), "#9ecbe1")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 200, width, height), fill="#71936c")
    for x, y, w, h, color in [(15, 135, 170, 145, "#8da3ad"), (210, 100, 110, 185, "#6c8393"),
                              (695, 115, 150, 175, "#a1adb1"), (865, 160, 100, 130, "#78909e")]:
        draw.rectangle((x, y, x + w, y + h), fill=color)
        for wx in range(x + 15, x + w - 10, 25):
            for wy in range(y + 15, y + h - 10, 28):
                draw.rectangle((wx, wy, wx + 10, wy + 13), fill="#d0dfe3")
    draw.polygon([(420, 240), (540, 240), (900, 540), (60, 540)], fill="#485363")
    draw.line([(420, 242), (60, 540)], fill="#dbe4e4", width=5)
    draw.line([(540, 242), (900, 540)], fill="#dbe4e4", width=5)
    phase = (vehicle.distance_m % 20) / 20
    for i in range(8):
        perspective = ((i + phase) / 8) ** 1.8
        y = int(242 + 298 * perspective)
        half = int(2 + 7 * perspective)
        length = int(5 + 30 * perspective)
        draw.rectangle((480 - half, y, 480 + half, min(height, y + length)), fill="#f6dfa1")
    draw.rounded_rectangle((384, 386, 576, 505), radius=27, fill="#153b5b", outline="#d0e6ed", width=3)
    draw.rounded_rectangle((404, 397, 556, 441), radius=11, fill="#8ec6d9")
    draw.rectangle((394, 467, 420, 478), fill="#fa7567")
    draw.rectangle((540, 467, 566, 478), fill="#fa7567")
    draw.rectangle((0, 0, width, 93), fill="#102a40")
    draw.text((24, 15), "LOCAL EDGE DEMO", font=_font(25), fill="#ffffff")
    draw.text((24, 53), "Illustrated camera - virtual vehicle - real API requests", font=_font(18), fill="#bcd7e6")
    state = "ROUTE RUNNING" if vehicle.running else "ROUTE STOPPED"
    draw.rounded_rectangle((707, 17, 935, 61), radius=10, fill="#22674e" if vehicle.running else "#526377")
    draw.text((722, 29), state, font=_font(17), fill="white")
    geo = DemoMap().transform_to_geolocation(vehicle.get_location())
    speed = vehicle.speed_mps * 3.6 if vehicle.running else 0
    draw.rectangle((0, 505, width, height), fill="#102a40")
    draw.text((20, 512), f"Car {car_id}   |   {speed:.1f} km/h   |   {geo.latitude:.5f}, {geo.longitude:.5f}",
              font=_font(17), fill="white")
    draw.text((22, 110), "Synthetic test tones" if synthetic_audio else "Recorded sound scenarios",
              font=_font(16), fill="#102a40")
    stream = BytesIO()
    image.save(stream, format="JPEG", quality=85)
    return stream.getvalue()


class DemoEdgeClient(CarlaEdgeClient):
    def __init__(self, *args, synthetic_audio=False, **kwargs):
        super().__init__(*args, wifi_apply=lambda device_id, settings: True, **kwargs)
        self.synthetic_audio = synthetic_audio

    def _execute(self, device, command, position):
        if command["command"] == "reboot":
            self.vehicle.set_autopilot(False, self.traffic_manager_port)
            self.capture_enabled = self.camera_capture_enabled = True
            return True, "Virtual edge restarted; host machine was not rebooted"
        ok, detail = super()._execute(device, command, position)
        if command["command"] == "sync_config" and ok:
            detail = "Virtual Wi-Fi configuration accepted; host network unchanged"
        return ok, detail

    def tick(self, seconds):
        self.vehicle.advance(seconds)
        if self.camera:
            self.set_frame(render_demo_frame(self.vehicle, car_id=self.car_id,
                                             synthetic_audio=self.synthetic_audio))
        self.step()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buffer-dir", type=Path, default=Path(".local_edge/demo"))
    audio_options = parser.add_mutually_exclusive_group()
    audio_options.add_argument("--scenarios", type=Path, help="Override bundled recordings with a five-category WAV/MP3 JSON mapping")
    audio_options.add_argument("--synthetic-audio", action="store_true", help="Generate synthetic test tones instead of bundled ESC-50 recordings")
    parser.add_argument("--no-camera", action="store_true", help="Run microphone/position only")
    parser.add_argument("--heartbeat-seconds", type=float, default=2.0)
    parser.add_argument("--no-audio-playback", action="store_true", help="Use browser preview instead of an installed local sound player")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if not 0.2 <= args.heartbeat_seconds <= 30:
            raise ValueError("--heartbeat-seconds must be between 0.2 and 30")
        mic = _device_from_env("EDGE_DEVICE")
        camera = None if args.no_camera else _device_from_env("EDGE_CAMERA_DEVICE")
        if camera and camera.id == mic.id:
            raise ValueError("Microphone and camera must be different registered devices")
        car_id = int(os.environ["EDGE_CAR_ID"])
        if car_id < 1:
            raise ValueError("EDGE_CAR_ID must be positive")
        scenarios = (make_synthetic_scenarios(args.buffer_dir / "synthetic-clips") if args.synthetic_audio
                     else load_scenarios(args.scenarios or BUNDLED_SCENARIOS))
        for category, path in scenarios.items():
            if not path.is_file():
                raise ValueError(f"Missing {category} scenario recording: {path}")
    except (KeyError, ValueError, OSError) as exc:
        parser.error(str(exc))
    firmware = "local-demo-synth-v1" if args.synthetic_audio else "local-demo-real-v1"
    cloud = CloudTransport(os.environ.get("EDGE_API_URL", "http://localhost:8000"), firmware=firmware)
    audio_player = SystemAudioPlayer(enabled=not args.no_audio_playback)
    adapter = DemoEdgeClient(DemoVehicle(), DemoMap(), cloud, mic, car_id,
                             EdgeBuffer(args.buffer_dir), scenarios, camera=camera,
                             synthetic_audio=args.synthetic_audio, play_audio=audio_player)
    log.info("LOCAL EDGE DEMO: virtual car %s; real cloud requests; %s", car_id,
             "synthetic test tones (not accuracy evidence)" if args.synthetic_audio else
             "user-provided recordings" if args.scenarios else "bundled ESC-50 recordings (CC BY-NC 3.0; see demo_clips/README.md)")
    log.info("Open Simulator as IoT Staff. Start route, then select a sound scenario.")
    log.info("Scenario sound output: %s", audio_player.description)
    stop, previous = Event(), time.monotonic()
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                adapter.tick(min(started - previous, 30.0))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403, 404):
                    raise SystemExit("Device rejected or deleted; verify ids, keys and car assignment") from None
                log.warning("Cloud request failed (%s); retained queued audio", exc.response.status_code)
            except (httpx.HTTPError, OSError, RuntimeError):
                log.warning("Cloud unavailable; retained queued audio and will retry")
            previous = started
            stop.wait(max(0.0, args.heartbeat_seconds - (time.monotonic() - started)))
    except KeyboardInterrupt:
        stop.set()
    finally:
        try:
            audio_player.close()
        finally:
            cloud.close()


if __name__ == "__main__":
    main()
