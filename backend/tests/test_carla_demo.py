"""Ownership and virtual device semantics; no claim of real CARLA rendering."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from edge.carla_demo import AdvancingCarlaWorld, CarlaDemoClient, OwnedCarlaVehicle
from tests.test_edge_client import command, setup  # noqa: F401 - isolated edge fixture
from tests.test_devices import client, create, device_env  # noqa: F401
from tests.auth_headers import bearer


def world_fixture(*, camera_failure=False):
    events = []
    class Blueprint:
        def __init__(self, id):
            self.id, self.attributes = id, {}
        def has_attribute(self, name):
            return name in ("number_of_wheels", "role_name")
        def get_attribute(self, name):
            return SimpleNamespace(as_int=lambda: 4)
        def set_attribute(self, name, value):
            self.attributes[name] = value
    vehicle_blueprint, camera_blueprint = Blueprint("vehicle.tesla.model3"), Blueprint("sensor.camera.rgb")
    class Actor:
        def __init__(self, id):
            self.id = id
        def destroy(self):
            events.append(("destroy", self.id))
            return True
        def set_autopilot(self, enabled, port):
            events.append(("autopilot", self.id, enabled, port))
        def stop(self):
            events.append(("stop", self.id))
        def listen(self, callback):
            self.callback = callback
    foreign, vehicle, camera = Actor(99), Actor(101), Actor(102)
    def spawn(blueprint, transform, attach_to):
        assert attach_to is vehicle
        if camera_failure:
            raise RuntimeError("camera setup failed")
        return camera
    world = SimpleNamespace(
        get_blueprint_library=lambda: SimpleNamespace(filter=lambda pattern: [vehicle_blueprint], find=lambda id: camera_blueprint),
        get_map=lambda: SimpleNamespace(get_spawn_points=lambda: [object()]),
        try_spawn_actor=lambda blueprint, transform: vehicle,
        spawn_actor=spawn,
    )
    carla = SimpleNamespace(Transform=lambda location: location, Location=lambda **values: values)
    return world, carla, events, camera_blueprint, foreign


@pytest.mark.parametrize("camera_failure", [False, True])
def test_owned_actor_cleanup_preserves_foreign_actors_even_after_partial_failure(camera_failure):
    world, carla, events, blueprint, foreign = world_fixture(camera_failure=camera_failure)
    with pytest.raises(RuntimeError):
        with OwnedCarlaVehicle(world, carla) as owned:
            owned.attach_camera(lambda image: None)
            assert blueprint.attributes["sensor_tick"] == "0.0"
            assert blueprint.attributes["image_size_x"] == "320"
            assert blueprint.attributes["image_size_y"] == "180"
            raise RuntimeError("edge stopped")
    assert ("destroy", 101) in events
    assert ("destroy", foreign.id) not in events
    if not camera_failure:
        assert ("stop", 102) in events and ("destroy", 102) in events
        assert events.index(("destroy", 102)) < events.index(("destroy", 101))


def test_carla_demo_virtual_configuration_and_restart_are_explicit(setup):
    previous, cloud = setup
    adapter = CarlaDemoClient(previous.vehicle, previous.map, previous.cloud, previous.mic,
                              previous.car_id, previous.buffer, previous.scenarios)
    adapter.capture_enabled = adapter.camera_capture_enabled = False
    cloud.commands[6] = [command(91, "sync_config"), command(92, "reboot")]
    adapter.step()
    assert adapter.capture_enabled and adapter.camera_capture_enabled
    assert all(ack["ok"] and "host" in ack["detail"] and "unchanged" in ack["detail"] for _, ack in cloud.acks)
    assert previous.vehicle.autopilot_calls == [(False, 8001)]


def test_actual_carla_runner_firmware_selects_carla_ui_mode(client):
    device = create(client, wifi={"ssid": "CARLA test", "security": "open"})
    response = client.post(f"/devices/{device['device']['id']}/heartbeat",
                           headers={"X-Device-Key": device["device_key"]},
                           json={"firmware": "carla-demo-v1", "lat": 37.3, "lon": -121.9, "town": "Town02"})
    assert response.status_code == 200
    state = client.get("/simulator/10/state", headers=bearer("iot_staff")).json()
    assert state["edge_mode"] == "carla" and state["position"]["town"] == "Town02"


@pytest.mark.parametrize("snapshot", [None, SimpleNamespace(frame=10), SimpleNamespace(frame=9)])
def test_frozen_or_regressed_world_cannot_send_heartbeats_or_uploads(snapshot):
    world = SimpleNamespace(wait_for_tick=Mock(return_value=snapshot))
    adapter = SimpleNamespace(step=Mock())
    monitor = AdvancingCarlaWorld(world, frame=10, timeout=30)
    with pytest.raises(RuntimeError, match="world is not advancing"):
        monitor.step(adapter)
    world.wait_for_tick.assert_called_once_with(30)
    adapter.step.assert_not_called()
    assert monitor.frame == 10


def test_world_rpc_failure_never_permits_cached_edge_step():
    world = SimpleNamespace(wait_for_tick=Mock(side_effect=RuntimeError("server disconnected")))
    adapter = SimpleNamespace(step=Mock())
    monitor = AdvancingCarlaWorld(world, frame=10, timeout=30)
    with pytest.raises(RuntimeError, match="server disconnected"):
        monitor.step(adapter)
    adapter.step.assert_not_called()


def test_only_new_world_ticks_permit_edge_steps():
    world = SimpleNamespace(wait_for_tick=Mock(side_effect=[SimpleNamespace(frame=11), SimpleNamespace(frame=12)]))
    adapter = SimpleNamespace(step=Mock())
    monitor = AdvancingCarlaWorld(world, frame=10, timeout=30)
    monitor.step(adapter)
    monitor.step(adapter)
    assert monitor.frame == 12 and adapter.step.call_count == 2


def test_cloud_failure_does_not_allow_the_same_frozen_tick_to_be_reused():
    world = SimpleNamespace(wait_for_tick=Mock(return_value=SimpleNamespace(frame=11)))
    adapter = SimpleNamespace(step=Mock(side_effect=OSError("cloud temporarily unavailable")))
    monitor = AdvancingCarlaWorld(world, frame=10, timeout=30)
    with pytest.raises(OSError):
        monitor.step(adapter)
    assert monitor.frame == 11
    with pytest.raises(RuntimeError, match="world is not advancing"):
        monitor.step(adapter)
    adapter.step.assert_called_once()
