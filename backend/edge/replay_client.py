"""Replay recorded CARLA scenarios as a live edge client (no GPU or CARLA server needed).

Each scenario button in the Simulator (F11) sends `simulate_event`. This client then plays the
recording for that sound class:
  - every heartbeat it reports the recorded position, speed and heading, and uploads the recorded
    camera frame for that moment;
  - at each event time in scenario.json it uploads the sound clip for the class (C3 -> C4 alert)
    and only then acknowledges the command, so the Simulator plays the sound at the event time.
Start route / Stop route resume and pause the current recording.

Recordings come from record_scenarios.py, one folder per scenario:
  <recordings-dir>/<name>/frames/*.jpg, telemetry.csv (t_s, file, lat, lon, speed_kmh, heading, ...),
  scenario.json (sound_class, town, events: [{t_s, label}]).

Run from backend/ with the same EDGE_* settings as the other edge clients:
  EDGE_API_URL=http://localhost:8000 EDGE_CAR_ID=1 EDGE_DEVICE_ID=1 EDGE_DEVICE_KEY=dev-device-key-mic-1 \\
  EDGE_CAMERA_DEVICE_ID=5 EDGE_CAMERA_DEVICE_KEY=dev-device-key-cam-1 python -m edge.replay_client
Stop any other edge client using the same devices first.
"""
import argparse
import csv
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Event
from uuid import NAMESPACE_URL, uuid5

import httpx
from cryptography.fernet import InvalidToken
from PIL import Image

from app.config import SOUND_CATEGORIES
from app.services.edge_buffer import EdgeBuffer
from app.services.errors import ServiceError
from app.services.ingest_service import MAX_AUDIO_BYTES, validate_audio
from edge.audio_playback import SystemAudioPlayer
from edge.carla_client import CarlaEdgeClient, CloudTransport, _device_from_env, load_scenarios

log = logging.getLogger(__name__)

EDGE_DIR = Path(__file__).resolve().parent
DEFAULT_RECORDINGS_DIR = EDGE_DIR / "carla_recordings"
DEFAULT_CLIPS = EDGE_DIR / "edge_scenarios.json"
# Which recording each Simulator button replays.
RECORDINGS = {
    "emergency": "emergency",
    "in_vehicle": "in_vehicle_crash",
    "human": "human_pullover",
    "traffic": "traffic",
    "environmental": "environmental",
}
# Sound event time (seconds into the replay) that replaces the recorded one, per sound class.
# environmental: the storm is there from the first frame, so the sound starts with the click.
# traffic: other cars and motorbikes become clearly visible on the street at 6 s (frame 7).
# human (pull-over): the brick wall appears at 13 s and the car stops at 14 s; scream just after.
# in_vehicle (crash): the car hits the parked car at 5 s (frame 6); crash sound one frame later.
EVENT_TIME_OVERRIDES = {"environmental": 0.0, "traffic": 6.0, "human": 15.0, "in_vehicle": 6.0}
FRAME_QUALITY = 90            # JPEG quality of the stored recordings and of any re-encoded frame
IDLE_FRAME_SECONDS = 5.0    # re-send the still frame this often when nothing is playing
DEFERRED = object()           # _execute result: acknowledge later, at the event time


@dataclass
class Recording:
    name: str
    category: str
    town: str
    rows: list[dict]
    events: list[dict]
    directory: Path

    @property
    def duration(self) -> float:
        return self.rows[-1]["t_s"] if self.rows else 0.0

    def frame_bytes(self, index: int) -> bytes:
        path = self.directory / self.rows[index]["file"]
        data = path.read_bytes()
        # Stored frames are clean quality-90 JPEGs: send them unchanged, without a second compression.
        if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            return data
        # A raw CARLA save_to_disk frame has padding after the JPEG end marker, which the API
        # rejects (415). Re-encode it at the same quality as the stored recordings.
        with Image.open(path) as image:
            stream = BytesIO()
            image.convert("RGB").save(stream, format="JPEG", quality=FRAME_QUALITY)
        return stream.getvalue()


def load_recording(directory: Path, category: str) -> Recording:
    meta = json.loads((directory / "scenario.json").read_text())
    rows = []
    with (directory / "telemetry.csv").open(newline="") as stream:
        for raw in csv.DictReader(stream):
            if not raw.get("file") or not (directory / raw["file"]).is_file():
                continue
            rows.append({"t_s": float(raw["t_s"]), "file": raw["file"],
                         "lat": float(raw["lat"]), "lon": float(raw["lon"]),
                         "speed_kmh": max(0.0, float(raw.get("speed_kmh") or 0.0)),
                         "heading": float(raw.get("heading") or 0.0) % 360})
    if not rows:
        raise ValueError(f"{directory.name}: no frames listed in telemetry.csv")
    rows.sort(key=lambda r: r["t_s"])
    # Time 0 is the first recorded frame, so replay starts immediately.
    start = rows[0]["t_s"]
    for row in rows:
        row["t_s"] = round(row["t_s"] - start, 3)
    events = sorted(({"t_s": max(0.0, float(e["t_s"]) - start), "label": e.get("label") or category}
                     for e in meta.get("events", [])), key=lambda e: e["t_s"])
    if category in EVENT_TIME_OVERRIDES:
        for event in events:
            event["t_s"] = EVENT_TIME_OVERRIDES[category]
    town = str(meta.get("town") or "CARLA").rsplit("/", 1)[-1][:100]
    return Recording(directory.name, category, town, rows, events, directory)


@dataclass
class Playback:
    recording: Recording
    device: object = None
    command_id: int | None = None
    elapsed: float = 0.0
    index: int = 0
    playing: bool = True
    paused: bool = False
    fired: set = field(default_factory=set)
    acknowledged: bool = False


class ReplayEdgeClient(CarlaEdgeClient):
    def __init__(self, cloud, mic, car_id, buffer, clips, recordings: dict[str, Recording], *,
                 camera=None, play_audio=None):
        super().__init__(None, None, cloud, mic, car_id, buffer, clips, camera=camera,
                         wifi_apply=lambda device_id, settings: True, play_audio=play_audio)
        self.recordings = recordings
        first = next(iter(recordings.values()))
        # Before any scenario: the first recording's opening frame, standing still.
        self.playback = Playback(first, playing=False)
        self._frame_index = None
        self._frame_sent_at = 0.0

    # ---- telemetry -------------------------------------------------------------------------
    def telemetry(self):
        pb = self.playback
        row = pb.recording.rows[pb.index]
        moving = pb.playing and not pb.paused
        return {"lat": row["lat"], "lon": row["lon"], "town": pb.recording.town,
                "speed_kmh": row["speed_kmh"] if moving else 0.0, "heading": row["heading"]}

    # ---- playback --------------------------------------------------------------------------
    def advance(self, seconds: float):
        pb = self.playback
        if not pb.playing or pb.paused:
            return
        pb.elapsed += seconds
        rows = pb.recording.rows
        while pb.index + 1 < len(rows) and rows[pb.index + 1]["t_s"] <= pb.elapsed:
            pb.index += 1
        for number, event in enumerate(pb.recording.events):
            if number not in pb.fired and event["t_s"] <= pb.elapsed:
                self._fire(pb, number, event)
        if pb.elapsed >= pb.recording.duration:
            # A late event still produces its clip, so every scenario creates an alert.
            for number, event in enumerate(pb.recording.events):
                if number not in pb.fired:
                    self._fire(pb, number, event)
            pb.playing = False
            if not pb.acknowledged:
                self._acknowledge(pb, True, f"Replayed {pb.recording.name}; it has no sound event")
            log.info("Finished %s", pb.recording.name)

    def _fire(self, pb: Playback, number: int, event: dict):
        pb.fired.add(number)
        source = self.scenarios.get(pb.recording.category)
        row = pb.recording.rows[pb.index]
        try:
            with source.open("rb") as stream:
                audio = stream.read(MAX_AUDIO_BYTES + 1)
            content_type = "audio/mpeg" if source.suffix.lower() == ".mp3" else "audio/wav"
            validate_audio(audio, content_type)
            # Stable across retries and restarts: one clip per command and event.
            clip_id = str(uuid5(NAMESPACE_URL, f"{self.cloud.base_url}/devices/{self.mic.id}/commands/"
                                               f"{pb.command_id}/events/{number}"))
            self.buffer.enqueue(audio, {"clip_id": clip_id, "device_id": self.mic.id, "car_id": self.car_id,
                                        "content_type": content_type, "trigger": pb.recording.category,
                                        "captured_at": datetime.now(timezone.utc).isoformat(),
                                        "lat": row["lat"], "lon": row["lon"]})
        except (OSError, ValueError, BufferError, ServiceError) as exc:
            log.warning("Could not buffer the %s clip (%s)", pb.recording.category, type(exc).__name__)
            self._acknowledge(pb, False, "Sound clip could not be buffered; check the scenario clip")
            return
        if self.play_audio is not None:
            try:
                self.play_audio(source)
            except Exception:  # local speakers are optional
                pass
        log.info("%s at %.1f s: %s (clip buffered)", pb.recording.name, event["t_s"], event["label"])
        self._acknowledge(pb, True, f"Sound at {event['t_s']:.1f} s: {event['label']}")

    def _acknowledge(self, pb: Playback, ok: bool, detail: str):
        if pb.acknowledged or pb.device is None or pb.command_id is None:
            pb.acknowledged = True
            return
        identity = (pb.device.id, pb.command_id)
        self._results[identity] = (ok, detail)
        self._acks[identity] = (pb.device, ok, detail)
        pb.acknowledged = True

    # ---- commands --------------------------------------------------------------------------
    def _execute(self, device, command, position):
        name = command["command"]
        pb = self.playback
        if name in ("start_route", "stop_route"):
            if name == "stop_route":
                pb.paused = True
                return True, f"Paused {pb.recording.name}" if pb.playing else "Car stopped"
            pb.paused = False
            return True, (f"Resumed {pb.recording.name}" if pb.playing
                          else "No recording is playing; choose a sound scenario")
        if name == "reboot":
            self._acknowledge(pb, False, "Replay client restarted")
            self.playback = Playback(pb.recording, playing=False)
            self.capture_enabled = self.camera_capture_enabled = True
            return True, "Replay client restarted; host machine was not rebooted"
        if name != "simulate_event":
            ok, detail = super()._execute(device, command, position)   # capture, sync_config, unsupported
            if name == "sync_config" and ok:
                detail = "Virtual Wi-Fi configuration accepted; host network unchanged"
            return ok, detail
        if not self.capture_enabled:
            return False, "Scenario capture is stopped; send start_capture first"
        category = (command.get("payload") or {}).get("scenario")
        recording = self.recordings.get(category)
        if recording is None:
            return False, f"No CARLA recording is configured for {category}"
        self._acknowledge(pb, False, "Replaced by another scenario before its sound event")
        self.playback = Playback(recording, device=device, command_id=command["command_id"])
        self._frame_index = None
        log.info("Replaying %s for %s (%d frames, events at %s s)", recording.name, category,
                 len(recording.rows), [round(e["t_s"], 1) for e in recording.events])
        return DEFERRED

    # ---- one heartbeat ---------------------------------------------------------------------
    def step(self):
        position = self.telemetry()
        for device in (self.mic, self.camera):
            if device is None:
                continue
            for command in self.cloud.heartbeat(device, position):
                identity = (device.id, command["command_id"])
                if identity in self._results:
                    self._acks[identity] = (device, *self._results[identity])
                    continue
                if self.playback.command_id == command["command_id"] and self.playback.device is device:
                    continue   # already replaying; acknowledged at the event time
                try:
                    result = self._execute(device, command, position)
                except (OSError, ValueError, RuntimeError, BufferError, InvalidToken, ServiceError):
                    result = (False, "Command failed; check the recordings and configuration")
                if result is DEFERRED:
                    self.advance(0.0)   # a sound event at 0 s fires in this same heartbeat
                    continue
                self._results[identity] = result
                if len(self._results) > 256:
                    self._results.popitem(last=False)
                self._acks[identity] = (device, *result)
        self._retry_acks()

        def send(metadata, audio):
            try:
                return self.cloud.upload(self.mic, metadata, audio)
            except (httpx.HTTPError, ValueError):
                return False
        self.buffer.flush(send, limit=1)

        if self.camera and self.camera_capture_enabled:
            pb, now = self.playback, time.monotonic()
            if pb.index != self._frame_index or now - self._frame_sent_at >= IDLE_FRAME_SECONDS:
                self.cloud.frame(self.camera, pb.recording.frame_bytes(pb.index))
                self._frame_index, self._frame_sent_at = pb.index, now

    def tick(self, seconds: float):
        self.advance(seconds)
        self.step()


def load_recordings(directory: Path) -> dict[str, Recording]:
    recordings = {}
    for category, name in RECORDINGS.items():
        try:
            recordings[category] = load_recording(directory / name, category)
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Skipping %s: %s", name, exc)
    if not recordings:
        raise ValueError(f"No usable recordings in {directory}")
    return recordings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS_DIR)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_CLIPS,
                        help="Five-category JSON mapping each sound class to its WAV/MP3 clip")
    parser.add_argument("--buffer-dir", type=Path, default=Path(".local_edge/replay"))
    parser.add_argument("--heartbeat-seconds", type=float, default=1.0)
    parser.add_argument("--no-audio-playback", action="store_true",
                        help="Do not play the clip on this machine's speakers (the browser still plays it)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if not 0.2 <= args.heartbeat_seconds <= 30:
            raise ValueError("--heartbeat-seconds must be between 0.2 and 30")
        mic = _device_from_env("EDGE_DEVICE")
        camera = _device_from_env("EDGE_CAMERA_DEVICE")
        if camera.id == mic.id:
            raise ValueError("Microphone and camera must be different registered devices")
        car_id = int(os.environ["EDGE_CAR_ID"])
        if car_id < 1:
            raise ValueError("EDGE_CAR_ID must be positive")
        clips = load_scenarios(args.scenarios)
        for category, path in clips.items():
            if not path.is_file():
                raise ValueError(f"Missing {category} sound clip: {path}")
        recordings = load_recordings(args.recordings_dir)
    except (KeyError, ValueError, OSError) as exc:
        parser.error(str(exc))

    cloud = CloudTransport(os.environ.get("EDGE_API_URL", "http://localhost:8000"), firmware="carla-replay-v1")
    audio_player = SystemAudioPlayer(enabled=not args.no_audio_playback)
    client = ReplayEdgeClient(cloud, mic, car_id, EdgeBuffer(args.buffer_dir), clips, recordings,
                              camera=camera, play_audio=audio_player)
    log.info("CARLA replay: car %s, %d recordings (%s)", car_id, len(recordings),
             ", ".join(f"{c}={r.name}" for c, r in recordings.items()))
    missing = [c for c in SOUND_CATEGORIES if c not in recordings]
    if missing:
        log.warning("No recording for: %s (those buttons will fail)", ", ".join(missing))
    log.info("Open the Simulator as IoT Staff and choose a sound scenario.")
    stop, previous = Event(), time.monotonic()
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                client.tick(min(started - previous, 30.0))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403, 404):
                    raise SystemExit("Device rejected or deleted; verify ids, keys and car assignment") from None
                log.warning("Cloud request failed (%s); queued audio is kept", exc.response.status_code)
            except (httpx.HTTPError, OSError, RuntimeError):
                log.warning("Cloud unavailable; queued audio is kept and will be retried")
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
