"""Executable local edge demo, including real HTTP routes and the packaged CNN.

Storage transport is isolated; commands, key authentication, validation, processing
and inference run through their normal implementations.
"""
from io import BytesIO
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from app.config import SOUND_CATEGORIES
from app.db import mongo, postgres, s3
from app.db.models import AlertTypeConfig, ModelVersion
from app.db.repositories.record_repo import RecordRepo
from app.services.auth_service import issue_token
from app.services.edge_buffer import EdgeBuffer
from app.services.ingest_service import validate_audio
from app.services.orchestrator import MLOrchestrator
from app.services.processing_worker import ProcessingWorker
from edge.carla_client import CloudTransport, DeviceCredentials, load_scenarios
from edge.demo_client import BUNDLED_SCENARIOS, DemoEdgeClient, DemoMap, DemoVehicle, make_synthetic_scenarios, render_demo_frame
from tests.test_devices import client, device_env  # noqa: F401 - reusable isolated store fixtures


def test_virtual_route_moves_only_while_running_and_sends_valid_camera():
    vehicle = DemoVehicle()
    before = vehicle.get_location()
    vehicle.advance(5)
    assert vehicle.get_location() == before
    vehicle.set_autopilot(True, 8001)
    vehicle.advance(5)
    assert vehicle.get_location() != before
    velocity = vehicle.get_velocity()
    assert (velocity.x ** 2 + velocity.y ** 2) ** 0.5 == pytest.approx(vehicle.speed_mps)
    geo = DemoMap().transform_to_geolocation(vehicle.get_location())
    assert 37.33 < geo.latitude < 37.34 and -121.89 < geo.longitude < -121.87
    running_frame = render_demo_frame(vehicle, car_id=10, synthetic_audio=True)
    with Image.open(BytesIO(running_frame)) as image:
        assert image.format == "JPEG" and image.size == (960, 540)
    vehicle.set_autopilot(False, 8001)
    stopped = vehicle.get_location()
    vehicle.advance(5)
    assert vehicle.get_location() == stopped
    assert vehicle.get_velocity().x == vehicle.get_velocity().y == 0
    assert running_frame != render_demo_frame(vehicle, car_id=10, synthetic_audio=True)


def test_synthetic_fixtures_are_distinct_valid_labeled_and_preserve_existing_files(tmp_path):
    paths = make_synthetic_scenarios(tmp_path)
    assert set(paths) == set(SOUND_CATEGORIES)
    assert len({path.read_bytes() for path in paths.values()}) == 5
    for path in paths.values():
        ext, mime, duration = validate_audio(path.read_bytes(), "audio/wav")
        assert (ext, mime, duration) == ("wav", "audio/wav", 3.0)
    assert "not real sirens" in (tmp_path / "README.txt").read_text()
    paths["human"].write_bytes(b"keep user file")
    make_synthetic_scenarios(tmp_path)
    assert paths["human"].read_bytes() == b"keep user file"


def test_bundled_recordings_match_attributed_manifest_and_upload_limits():
    scenarios = load_scenarios(BUNDLED_SCENARIOS)
    assert load_scenarios(BUNDLED_SCENARIOS.parent.parent / "edge_scenarios.json") == scenarios
    manifest = json.loads(BUNDLED_SCENARIOS.with_name("manifest.json").read_text())
    assert set(scenarios) == set(SOUND_CATEGORIES)
    assert len(manifest["clips"]) == 5
    assert manifest["dataset_license_url"] == "https://creativecommons.org/licenses/by-nc/3.0/"
    for clip in manifest["clips"]:
        audio = scenarios[clip["scenario"]].read_bytes()
        assert hashlib.sha256(audio).hexdigest() == clip["sha256"]
        assert validate_audio(audio, "audio/wav") == ("wav", "audio/wav", 5.0)
        assert clip["original_author"] and clip["original_source_url"] and clip["esc50_source_url"]


@pytest.mark.parametrize("synthetic_audio", [False, True], ids=["bundled-recordings", "synthetic-fixtures"])
def test_local_edge_real_routes_five_uploads_and_packaged_model(client, device_env, tmp_path, monkeypatch, synthetic_audio):
    iot = {"Authorization": f"Bearer {issue_token(1, 'iot_staff')}"}
    cloud_staff = {"Authorization": f"Bearer {issue_token(1, 'cloud_staff')}"}
    wifi = {"ssid": "Virtual car", "security": "wpa2", "password": "virtual-password", "ip_mode": "dhcp"}

    def register(name, kind):
        response = client.post("/devices", headers=iot,
                               json={"car_id": 10, "name": name, "device_type": kind, "wifi": wifi})
        assert response.status_code == 201, response.text
        result = response.json()
        return DeviceCredentials(result["device"]["id"], result["device_key"])

    mic, camera = register("Demo microphone", "microphone"), register("Demo camera", "camera")
    firmware = "local-demo-synth-v1" if synthetic_audio else "local-demo-real-v1"
    transport = CloudTransport(str(client.base_url), client, firmware=firmware)
    scenarios = make_synthetic_scenarios(tmp_path / "clips") if synthetic_audio else load_scenarios(BUNDLED_SCENARIOS)
    adapter = DemoEdgeClient(DemoVehicle(), DemoMap(), transport, mic, 10,
                             EdgeBuffer(tmp_path / "buffer"), scenarios, camera=camera, synthetic_audio=synthetic_audio)
    mongo.ensure_indexes()
    adapter.tick(2)
    for device in (mic, camera):
        public = client.get(f"/devices/{device.id}", headers=iot).json()
        assert public["status"] == "online" and public["firmware"] == firmware
        history = client.get(f"/devices/{device.id}/commands", headers=iot).json()
        assert history[0]["status"] == "acknowledged"
    state = client.get("/simulator/10/state", headers=iot).json()
    assert state["online"] and state["frame_url"] and state["position"]["speed_kmh"] == 0
    assert device_env["objects"]["frames/10/latest.jpg"]["content_type"] == "image/jpeg"

    def command(name, payload=None):
        response = client.post(f"/devices/{mic.id}/commands", headers=iot,
                               json={"command": name, "payload": payload or {}})
        assert response.status_code == 202, response.text
        return response.json()["command_id"]

    command("start_route")
    adapter.tick(2)
    adapter.tick(2)
    moving = client.get("/simulator/10/state", headers=iot).json()
    assert moving["position"]["speed_kmh"] == pytest.approx(32.4)
    assert moving["position"]["lat"] != state["position"]["lat"]
    for category in SOUND_CATEGORIES:
        command("simulate_event", {"scenario": category})
        adapter.tick(2)
    records = client.get("/device-data?car_id=10", headers=iot).json()
    assert records["total"] == 5
    assert {record["trigger"] for record in records["items"]} == set(SOUND_CATEGORIES)
    assert all(record["status"] == "pending" for record in records["items"])
    assert len(adapter.buffer) == 0

    # Real shipped model; only raw object transport is replaced by an in-memory store.
    model_path = Path(__file__).resolve().parents[2] / "model" / "artifacts" / "cnn-v1"
    with postgres.session_scope() as session:
        session.add(ModelVersion(version_label="cnn-v1", is_active=True,
                                 confidence_threshold=0.75, artifact_path=str(model_path)))
        for category in SOUND_CATEGORIES:
            session.add(AlertTypeConfig(alert_type=category, default_severity="medium", enabled=True))
    monkeypatch.setattr(s3, "get_object", lambda key: device_env["objects"][key]["body"])
    orchestrator = MLOrchestrator()
    worker = ProcessingWorker(RecordRepo(), orchestrator)
    for _ in SOUND_CATEGORIES:
        assert worker.run_once()
    assert not worker.run_once()
    processed = client.get("/device-data?car_id=10", headers=cloud_staff).json()["items"]
    assert all(record["status"] == "processed" and record["alert_id"] for record in processed)
    alerts = list(device_env["db"].alerts.find({"car_id": 10}))
    assert len(alerts) == 5 and all(row["sound_type"] in SOUND_CATEGORIES for row in alerts)
    assert client.get("/simulator/10/state", headers=iot).json()["last_alert"]["id"]

    command("stop_route")
    adapter.tick(2)
    adapter.tick(2)
    assert client.get("/simulator/10/state", headers=iot).json()["position"]["speed_kmh"] == 0
    command("reboot")
    adapter.tick(2)
    assert not adapter.vehicle.running
    assert client.get(f"/devices/{mic.id}/commands", headers=iot).json()[0]["status"] == "acknowledged"
