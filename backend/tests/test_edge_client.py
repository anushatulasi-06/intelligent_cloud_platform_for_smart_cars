"""The optional edge adapter needs neither a running CARLA server nor cloud services."""
import base64
import hashlib
import json
import stat
import sys
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import httpx
import numpy as np
import pytest
import soundfile as sf
from cryptography.fernet import Fernet

from app.config import SOUND_CATEGORIES
from app.services.edge_buffer import EdgeBuffer
from edge import carla_client
from edge.carla_client import CarlaEdgeClient, CloudTransport, DeviceCredentials, load_scenarios


class Vehicle:
    type_id = "vehicle.test"

    def __init__(self):
        self.autopilot_calls = []
        self.control = SimpleNamespace(throttle=0.7, brake=0.0)

    def get_location(self):
        return SimpleNamespace(x=1, y=2, z=0)

    def get_velocity(self):
        return SimpleNamespace(x=3, y=4, z=0)

    def get_transform(self):
        return SimpleNamespace(rotation=SimpleNamespace(yaw=-90))

    def set_autopilot(self, enabled, port):
        self.autopilot_calls.append((enabled, port))

    def get_control(self):
        return self.control

    def apply_control(self, control):
        self.control = control


class MockCloud:
    def __init__(self):
        self.commands, self.acks, self.uploads, self.heartbeats, self.frames = {}, [], [], [], []
        self.fail_uploads, self.fail_acks = 0, 0

    def request(self, request):
        path = request.url.path
        if path.endswith("/heartbeat"):
            device_id = int(path.split("/")[2])
            self.heartbeats.append((device_id, request.headers["X-Device-Key"], json.loads(request.content)))
            return httpx.Response(200, json={"commands": self.commands.pop(device_id, [])})
        if path.endswith("/ack"):
            if self.fail_acks:
                self.fail_acks -= 1
                raise httpx.ReadTimeout("ambiguous response", request=request)
            self.acks.append((int(path.split("/")[4]), json.loads(request.content)))
            return httpx.Response(200, json={"status": "acknowledged"})
        if path == "/device-data":
            self.uploads.append(request)
            if self.fail_uploads:
                self.fail_uploads -= 1
                return httpx.Response(503, json={"detail": "storage offline"})
            return httpx.Response(202, json={"record_id": "saved-record", "status": "pending"})
        if path.endswith("/frame"):
            self.frames.append(request)
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {path}")


def command(id, name, payload=None):
    return {"command_id": id, "command": name, "payload": payload or {}}


@pytest.fixture
def setup(tmp_path):
    stream = BytesIO()
    sf.write(stream, np.zeros(1600), 16000, format="WAV")
    source = tmp_path / "clip.wav"
    source.write_bytes(stream.getvalue())
    mock = MockCloud()
    cloud = CloudTransport("https://cloud.test", httpx.Client(transport=httpx.MockTransport(mock.request)))
    vehicle = Vehicle()
    carla_map = SimpleNamespace(transform_to_geolocation=lambda location: SimpleNamespace(latitude=37.3, longitude=-121.9))
    mic = DeviceCredentials(6, "mic-secret")
    adapter = CarlaEdgeClient(vehicle, carla_map, cloud, mic, 1, EdgeBuffer(tmp_path / "buffer"),
                              {category: source for category in SOUND_CATEGORIES})
    yield adapter, mock
    cloud.close()


def test_route_control_capture_gate_and_unsupported_reboot(setup):
    adapter, cloud = setup
    cloud.commands[6] = [command(1, "start_route"), command(2, "stop_route"), command(3, "stop_capture"),
                         command(4, "simulate_event", {"scenario": "human"}), command(5, "reboot")]
    adapter.step()
    adapter.step()
    adapter.step()  # ACKs drain in bounded batches while heartbeats continue.
    assert adapter.vehicle.autopilot_calls == [(True, 8001), (False, 8001)]
    assert adapter.vehicle.control.throttle == 0 and adapter.vehicle.control.brake == 1
    assert [(id, ack["ok"]) for id, ack in cloud.acks] == [(1, True), (2, True), (3, True), (4, False), (5, False)]
    assert "not supported" in cloud.acks[-1][1]["detail"]
    assert not cloud.uploads
    device_id, key, position = cloud.heartbeats[0]
    assert device_id == 6 and key == "mic-secret"
    assert position == {"firmware": "carla-edge-v1", "lat": 37.3, "lon": -121.9, "speed_kmh": 18.0, "heading": 0}


def test_scenario_survives_offline_restart_and_keeps_clip_id(setup):
    adapter, cloud = setup
    cloud.commands[6] = [command(10, "simulate_event", {"scenario": "emergency"})]
    cloud.fail_uploads = 1
    adapter.step()
    assert len(adapter.buffer) == 1 and cloud.acks[-1][1]["ok"] is True
    path = adapter.buffer._entries()[0]
    metadata = json.loads(path.read_bytes().split(b"\n", 1)[0])
    assert metadata["trigger"] == "emergency" and metadata["device_id"] == 6
    assert "mic-secret" not in path.read_text(errors="ignore")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    restarted = CarlaEdgeClient(adapter.vehicle, adapter.map, adapter.cloud, adapter.mic, 1,
                                EdgeBuffer(adapter.buffer.directory), adapter.scenarios)
    restarted.step()
    assert len(restarted.buffer) == 0 and len(cloud.uploads) == 2
    for request in cloud.uploads:
        assert metadata["clip_id"].encode() in request.content
        assert request.headers["X-Device-Key"] == "mic-secret"


def test_backlog_recovery_keeps_heartbeats_between_fifo_uploads(setup):
    adapter, cloud = setup
    clip_ids = [str(uuid4()) for _ in range(3)]
    for clip_id in clip_ids:
        adapter.buffer.enqueue(b"saved clip", {"clip_id": clip_id, "device_id": 6, "car_id": 1,
                                               "content_type": "audio/wav"})
    for index, clip_id in enumerate(clip_ids, start=1):
        adapter.step()
        assert len(cloud.heartbeats) == index
        assert len(cloud.uploads) == index
        assert len(adapter.buffer) == len(clip_ids) - index
        assert clip_id.encode() in cloud.uploads[-1].content


def test_ack_backlog_is_bounded_per_heartbeat_pass(setup):
    adapter, cloud = setup
    cloud.commands[6] = [command(id, "start_capture") for id in range(4)]
    adapter.step()
    assert len(cloud.acks) == 2 and len(adapter._acks) == 2
    adapter.step()
    assert len(cloud.acks) == 4 and not adapter._acks and len(cloud.heartbeats) == 2


def test_duplicate_commands_and_ack_timeouts_do_not_repeat_side_effects(setup):
    adapter, cloud = setup
    cloud.commands[6] = [command(20, "start_route")]
    cloud.fail_acks = 1
    adapter.step()
    assert adapter._acks and not cloud.acks
    cloud.commands[6] = [command(20, "start_route")]
    adapter.step()
    assert not adapter._acks
    assert adapter.vehicle.autopilot_calls == [(True, 8001)]


def test_scenario_playback_is_once_per_command_and_never_blocks_durable_upload(setup):
    adapter, cloud = setup
    played = []
    adapter.play_audio = lambda source: played.append(source) or True
    cloud.commands[6] = [command(21, "simulate_event", {"scenario": "human"})]
    cloud.fail_acks = 1
    adapter.step()
    cloud.commands[6] = [command(21, "simulate_event", {"scenario": "human"})]
    adapter.step()
    assert played == [adapter.scenarios["human"]]
    assert len(cloud.uploads) == 1 and cloud.acks[-1][1]["ok"]
    assert "player started" in cloud.acks[-1][1]["detail"]
    def unavailable(source):
        raise OSError("No sound device")
    adapter.play_audio = unavailable
    cloud.commands[6] = [command(22, "simulate_event", {"scenario": "traffic"})]
    adapter.step()
    assert len(cloud.uploads) == 2 and cloud.acks[-1][1]["ok"]
    assert "browser preview" in cloud.acks[-1][1]["detail"]
    assert not adapter.buffer


def test_stopped_capture_never_plays_or_uploads_a_scenario(setup):
    adapter, cloud = setup
    played = []
    adapter.play_audio = lambda source: played.append(source) or True
    adapter.capture_enabled = False
    cloud.commands[6] = [command(23, "simulate_event", {"scenario": "human"})]
    adapter.step()
    assert not played and not cloud.uploads and not cloud.acks[-1][1]["ok"]


def test_system_audio_player_passes_path_as_one_argument_and_cleans_only_owned_child(tmp_path, monkeypatch):
    from edge import audio_playback
    calls, stopped = [], []
    monkeypatch.setattr(audio_playback.shutil, "which", lambda name: "/usr/bin/ffplay" if name == "ffplay" else None)
    def launch(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(poll=lambda: None, terminate=lambda: stopped.append("terminate"), wait=lambda timeout: 0)
    monkeypatch.setattr(audio_playback.subprocess, "Popen", launch)
    player = audio_playback.SystemAudioPlayer()
    source = tmp_path / "clip ; $(untrusted).wav"
    assert player(source)
    assert calls[0][0][-2:] == ["-i", str(source.resolve())]
    assert "shell" not in calls[0][1] and calls[0][1]["stdin"] == audio_playback.subprocess.DEVNULL
    player.close()
    assert stopped == ["terminate"]
    assert audio_playback.SystemAudioPlayer(enabled=False)(source) is False


def test_capture_resume_and_missing_or_invalid_scenario_fail_cleanly(setup):
    adapter, cloud = setup
    adapter.capture_enabled = False
    cloud.commands[6] = [command(30, "start_capture"), command(31, "simulate_event", {"scenario": "traffic"}),
                         command(32, "simulate_event", {"scenario": "not-a-category"})]
    adapter.step()
    adapter.step()
    assert [ack["ok"] for _, ack in cloud.acks] == [True, True, False]
    assert len(cloud.uploads) == 1
    adapter.scenarios["traffic"].write_bytes(b"corrupt WAV")
    cloud.commands[6] = [command(33, "simulate_event", {"scenario": "traffic"})]
    adapter.step()
    assert cloud.acks[-1][1]["ok"] is False and len(cloud.uploads) == 1


def test_wifi_is_encrypted_on_disk_and_requires_real_apply_confirmation(setup):
    adapter, cloud = setup
    cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"mic-secret").digest()))
    wifi = {"ssid": "Vehicle Wi-Fi", "security": "wpa2", "ip_mode": "dhcp",
            "password_enc": cipher.encrypt(b"wifi-secret").decode()}
    cloud.commands[6] = [command(40, "sync_config", {"wifi": wifi})]
    adapter.step()
    assert cloud.acks[-1][1]["ok"] is False
    saved = adapter.buffer.directory / "wifi" / "6.json"
    assert "wifi-secret" not in saved.read_text()
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    applied = []
    def apply(device_id, settings):
        applied.append((device_id, settings))
        return True
    adapter.wifi_apply = apply
    cloud.commands[6] = [command(41, "sync_config", {"wifi": wifi})]
    adapter.step()
    assert cloud.acks[-1][1]["ok"] is True
    assert applied[0][0] == 6 and applied[0][1]["password"] == "wifi-secret"


def test_camera_uses_its_own_device_key(setup):
    adapter, cloud = setup
    adapter.camera = DeviceCredentials(7, "camera-secret")
    adapter.set_frame(b"jpeg-frame")
    adapter.step()
    assert [(id, key) for id, key, _ in cloud.heartbeats] == [(6, "mic-secret"), (7, "camera-secret")]
    assert cloud.frames[0].url.path == "/devices/7/frame"
    assert cloud.frames[0].headers["X-Device-Key"] == "camera-secret"
    assert b"jpeg-frame" in cloud.frames[0].content
    cloud.commands[7] = [command(50, "stop_capture")]
    adapter.set_frame(b"next-frame")
    adapter.step()
    assert len(cloud.frames) == 1 and adapter.capture_enabled
    cloud.commands[7] = [command(51, "start_capture")]
    adapter.set_frame(b"resumed-frame")
    adapter.step()
    assert len(cloud.frames) == 2


def test_scenario_config_resolves_paths_and_enforces_five_categories(tmp_path):
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps({category: f"clips/{category}.wav" for category in SOUND_CATEGORIES}))
    assert load_scenarios(path)["human"] == tmp_path / "clips" / "human.wav"
    path.write_text('{"human": "human.wav"}')
    with pytest.raises(ValueError):
        load_scenarios(path)


def test_cli_cleans_up_only_its_camera_sensor(tmp_path, monkeypatch):
    events = []
    vehicle = Vehicle()
    sensor = SimpleNamespace(listen=lambda callback: events.append("listen"), stop=lambda: events.append("sensor.stop"),
                             destroy=lambda: events.append("sensor.destroy"))
    blueprint = SimpleNamespace(set_attribute=lambda key, value: None)
    def spawn(blueprint, transform, attach_to):
        assert attach_to is vehicle
        events.append("spawn.camera")
        return sensor
    world = SimpleNamespace(get_actor=lambda id: vehicle, get_map=lambda: object(), spawn_actor=spawn,
                            get_blueprint_library=lambda: SimpleNamespace(find=lambda id: blueprint))
    connection = SimpleNamespace(set_timeout=lambda value: None, get_world=lambda: world,
                                 get_trafficmanager=lambda port: events.append(("traffic_manager", port)))
    fake_carla = SimpleNamespace(Client=lambda host, port: connection,
                                 Transform=lambda location: location, Location=lambda **kwargs: kwargs)
    monkeypatch.setitem(sys.modules, "carla", fake_carla)
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=object()))
    monkeypatch.setattr(carla_client, "CloudTransport", lambda url: SimpleNamespace(close=lambda: events.append("cloud.close")))
    class StopImmediately:
        def __init__(self, *args, **kwargs):
            pass
        def step(self):
            raise KeyboardInterrupt
    monkeypatch.setattr(carla_client, "CarlaEdgeClient", StopImmediately)
    for name, value in {"EDGE_DEVICE_ID": "6", "EDGE_DEVICE_KEY": "mic-secret", "EDGE_CAR_ID": "1",
                        "EDGE_CAMERA_DEVICE_ID": "7", "EDGE_CAMERA_DEVICE_KEY": "camera-secret"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["edge.carla_client", "--actor-id", "42", "--camera", "--buffer-dir", str(tmp_path)])
    carla_client.main()
    assert events == [("traffic_manager", 8001), "spawn.camera", "listen", "sensor.stop", "sensor.destroy", "cloud.close"]
    assert not vehicle.autopilot_calls
