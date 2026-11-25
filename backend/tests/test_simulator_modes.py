"""Recorded replay and live simulator sources must remain distinguishable."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import postgres
from app.db.models import Device
from tests.test_devices import CLOUD, IOT, client, create, device_env


@pytest.mark.parametrize(("firmware", "mode"), [
    ("local-demo-real-v1", "local_demo"),
    ("carla-replay-v1", "carla_replay"),
    ("carla-demo-v1", "carla"),
    ("carla-edge-v1", "carla"),
    ("physical-edge-v1", "edge"),
])
def test_simulator_identifies_source_without_confusing_replay_with_live_carla(client, firmware, mode):
    registered = create(client)
    response = client.post(f"/devices/{registered['device']['id']}/heartbeat",
                           headers={"X-Device-Key": registered["device_key"]}, json={"firmware": firmware})
    assert response.status_code == 200
    for staff in (IOT, CLOUD):
        state = client.get("/simulator/10/state", headers=staff).json()
        assert state["online"] and state["edge_mode"] == mode
        if mode == "carla_replay":
            assert state["audio_source"] == "recordings"
        assert registered["device_key"] not in str(state)


def test_disconnected_replay_retains_recorded_source_for_its_stale_frame(client, device_env):
    registered = create(client)
    device_id = registered["device"]["id"]
    observed = datetime.now(timezone.utc) - timedelta(minutes=5)
    with postgres.session_scope() as session:
        device = session.get(Device, device_id)
        device.firmware, device.last_seen = "carla-replay-v1", observed
    device_env["objects"]["frames/10/latest.jpg"] = {
        "body": b"recorded frame", "content_type": "image/jpeg", "modified_at": observed}
    state = client.get("/simulator/10/state", headers=IOT).json()
    assert not state["online"] and state["edge_mode"] == "carla_replay"
    assert state["audio_source"] == "recordings" and state["frame_url"]
    assert datetime.fromisoformat(state["frame_at"].replace("Z", "+00:00")) == observed
