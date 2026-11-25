"""Replay recorded CARLA scenarios as a live edge client (no GPU or CARLA server needed).

Each scenario button in the Simulator (F11) sends `simulate_event`. This client then plays the
recording for that sound class:
  - every heartbeat it reports the recorded position, speed and heading, and uploads the recorded
    camera frame for that moment;
  - at each sound event it durably buffers the clip, then acknowledges the command;
    the normal FIFO upload and C3/C4 processing follow independently. ACK is not proof of processing.
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
import hashlib
import json
import logging
import math
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
from edge.carla_client import CarlaEdgeClient, CloudTransport, _device_from_env, _write_private, load_scenarios

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


def _frame_path(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or not name:
        raise ValueError("Recording frame filename is missing")
    relative = Path(name)
    path = (directory / relative).resolve()
    if (relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(directory.resolve())
            or not path.is_file()):
        raise ValueError("Recording frame must be an existing file inside its recording directory")
    return path


def _finite(value, description):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Recording {description} must be numeric") from None
    if not math.isfinite(result):
        raise ValueError(f"Recording {description} must be finite")
    return result


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
        path = _frame_path(self.directory, self.rows[index]["file"])
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
    directory = directory.resolve()
    meta = json.loads((directory / "scenario.json").read_text())
    if not isinstance(meta, dict) or category not in SOUND_CATEGORIES or meta.get("sound_class") != category:
        raise ValueError("Recording sound class must match its configured category")
    rows = []
    with (directory / "telemetry.csv").open(newline="") as stream:
        for raw in csv.DictReader(stream):
            path = _frame_path(directory, raw["file"])
            with Image.open(path) as image:
                if image.format != "JPEG":
                    raise ValueError("Recording frames must be JPEG images")
                image.verify()
            stamp = _finite(raw["t_s"], "frame time")
            lat, lon = _finite(raw["lat"], "latitude"), _finite(raw["lon"], "longitude")
            speed = _finite(raw.get("speed_kmh") or 0.0, "speed")
            if (stamp < 0 or (rows and stamp <= rows[-1]["t_s"]) or not -90 <= lat <= 90
                    or not -180 <= lon <= 180 or speed < 0):
                raise ValueError("Recording times must increase and telemetry must have valid coordinates/speed")
            rows.append({"t_s": stamp, "file": raw["file"], "lat": lat, "lon": lon,
                         "speed_kmh": speed, "heading": _finite(raw.get("heading") or 0.0, "heading") % 360})
    if not rows:
        raise ValueError(f"{directory.name}: no frames listed in telemetry.csv")
    # Time 0 is the first recorded frame, so replay starts immediately.
    start = rows[0]["t_s"]
    for row in rows:
        row["t_s"] = round(row["t_s"] - start, 3)
    raw_events = meta.get("events")
    if (not isinstance(raw_events, list) or not 1 <= len(raw_events) <= 1000
            or any(not isinstance(event, dict) or "t_s" not in event for event in raw_events)):
        raise ValueError("A sound scenario needs valid event metadata")
    events = sorted(({"t_s": max(0.0, _finite(e["t_s"], "event time") - start), "label": e.get("label") or category}
                     for e in raw_events), key=lambda e: e["t_s"])
    if not events or rows[-1]["t_s"] <= 0:
        raise ValueError("A sound scenario needs a positive recording duration and at least one event")
    if category in EVENT_TIME_OVERRIDES:
        for event in events:
            event["t_s"] = EVENT_TIME_OVERRIDES[category]
    if any(event["t_s"] > rows[-1]["t_s"] for event in events):
        raise ValueError("Recording sound events must fall inside the recorded duration")
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
    sound_cancelled: bool = False


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
        self._journal_path = self.buffer.directory / "replay-commands.json"
        self._journal = {}
        self._journal_identity = hashlib.sha256(
            f"{self.cloud.base_url}|{car_id}|{mic.id}|{camera.id if camera else None}".encode()).hexdigest()
        self._restore_commands()

    def _persist_commands(self):
        _write_private(self._journal_path, {"version": 1, "identity": self._journal_identity,
                                          "commands": list(self._journal.values())})

    def _save_outcome(self, identity, ok, detail):
        row = self._journal.setdefault(identity, {"device_id": identity[0], "command_id": identity[1], "events": 0})
        row.update(ok=bool(ok), detail=detail)
        self._persist_commands()

    def _restore_commands(self):
        if not self._journal_path.exists():
            return
        document = json.loads(self._journal_path.read_text())
        devices = {device.id: device for device in (self.mic, self.camera) if device is not None}
        if (not isinstance(document, dict) or document.get("version") != 1
                or document.get("identity") != self._journal_identity):
            raise ValueError("Replay command journal belongs to another API/device configuration; use a separate buffer")
        if not isinstance(document.get("commands"), list):
            raise ValueError("Replay command journal is invalid")
        for row in document["commands"]:
            if (not isinstance(row, dict) or type(row.get("command_id")) is not int or row["command_id"] <= 0
                    or row.get("device_id") not in devices or (row.get("ok") is not None and type(row["ok"]) is not bool)
                    or type(row.get("events")) is not int or not 0 <= row["events"] <= 1000):
                raise ValueError("Replay command journal is invalid")
            identity = (row["device_id"], row["command_id"])
            if row["ok"] is None:
                # A crash can occur between FIFO fsync and outcome fsync. Recover
                # that buffered receipt, otherwise explicitly fail the interrupted replay.
                buffered = any((self.buffer.directory / (str(uuid5(NAMESPACE_URL,
                    f"{self.cloud.base_url}/devices/{self.mic.id}/commands/{row['command_id']}/events/{number}"))
                    + ".clip")).is_file() for number in range(row["events"]))
                row.update(ok=buffered, detail=("Buffered sound recovered; upload/processing follow separately" if buffered
                           else "Replay interrupted before its sound event; choose the scenario again"))
            self._journal[identity] = row
            self._results[identity] = (row["ok"], row["detail"])
            self._acks[identity] = (devices[row["device_id"]], row["ok"], row["detail"])
        self._persist_commands()

    def _retry_acks(self):
        # The API delivers commands once. Keep outcomes until the edge receives
        # an ACK response; an ambiguous response/restart must not silently lose them.
        if self._journal:
            self._persist_commands()
        devices = {device.id: device for device in (self.mic, self.camera) if device is not None}
        for identity, row in self._journal.items():
            if row["ok"] is not None:
                self._acks[identity] = (devices[row["device_id"]], row["ok"], row["detail"])
        before = set(self._acks)
        super()._retry_acks()
        completed = before - set(self._acks)
        for identity in completed:
            self._journal.pop(identity, None)
        if completed:
            self._persist_commands()

    # ---- telemetry -------------------------------------------------------------------------
    def telemetry(self):
        pb = self.playback
        row = pb.recording.rows[pb.index]
        moving = pb.playing and not pb.paused
        return {"lat": row["lat"], "lon": row["lon"], "town": pb.recording.town,
                "speed_kmh": row["speed_kmh"] if moving else 0.0, "heading": row["heading"]}

    # ---- playback --------------------------------------------------------------------------
    def advance(self, seconds: float):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Replay elapsed time must be finite and nonnegative")
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
            pb.playing = False
            if not pb.acknowledged:
                self._acknowledge(pb, True, f"Replayed {pb.recording.name}; it has no sound event")
            log.info("Finished %s", pb.recording.name)

    def _fire(self, pb: Playback, number: int, event: dict):
        pb.fired.add(number)
        if pb.sound_cancelled or not self.capture_enabled:
            self._acknowledge(pb, False, "Replay sound cancelled because microphone capture stopped")
            return
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
        self._acknowledge(pb, True, f"Sound buffered at {event['t_s']:.1f} s; upload/processing follow separately")

    def _acknowledge(self, pb: Playback, ok: bool, detail: str):
        if pb.acknowledged or pb.device is None or pb.command_id is None:
            pb.acknowledged = True
            return
        identity = (pb.device.id, pb.command_id)
        self._save_outcome(identity, ok, detail)
        self._results[identity] = (ok, detail)
        if len(self._results) > 256:
            self._results.popitem(last=False)
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
            if not pb.playing:
                return False, "Choose a sound scenario first; Start route only resumes a paused recording"
            pb.paused = False
            return True, f"Resumed {pb.recording.name}"
        if name == "reboot":
            self._acknowledge(pb, False, "Replay client restarted")
            self.playback = Playback(pb.recording, playing=False)
            self._frame_index = None
            self.capture_enabled = self.camera_capture_enabled = True
            return True, "Replay client restarted; host machine was not rebooted"
        if name != "simulate_event":
            if name == "stop_capture" and device.id == self.mic.id:
                pb.sound_cancelled = True
                self._acknowledge(pb, False, "Replay sound cancelled because microphone capture stopped")
            ok, detail = super()._execute(device, command, position)   # capture, sync_config, unsupported
            if name == "start_capture" and self.camera and device.id == self.camera.id:
                self._frame_index = None
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
        self._journal[(device.id, command["command_id"])] = {"device_id": device.id,
            "command_id": command["command_id"], "ok": None, "detail": None,
            "events": len(recording.events)}
        self._persist_commands()
        self.playback = Playback(recording, device=device, command_id=command["command_id"])
        self._frame_index = None
        log.info("Replaying %s for %s (%d frames, events at %s s)", recording.name, category,
                 len(recording.rows), [round(e["t_s"], 1) for e in recording.events])
        return DEFERRED

    # ---- one heartbeat ---------------------------------------------------------------------
    def step(self, elapsed_seconds=0.0):
        previous_playback = self.playback
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
                    continue
                self._save_outcome(identity, *result)
                self._results[identity] = result
                if len(self._results) > 256:
                    self._results.popitem(last=False)
                self._acks[identity] = (device, *result)
        # Apply stop/pause commands before firing newly due events. A scenario received
        # in this heartbeat starts at zero, rather than inheriting the preceding interval.
        self.advance(elapsed_seconds if self.playback is previous_playback else 0.0)
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
        self.step(seconds)


def load_recordings(directory: Path) -> dict[str, Recording]:
    return {category: load_recording(directory / name, category) for category, name in RECORDINGS.items()}


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
