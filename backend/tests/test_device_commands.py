"""Command delivery, superseded Wi-Fi settings, frames and migration compatibility."""
import os
import subprocess
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError

from app.config import BACKEND_DIR
from app.db import postgres
from app.db.models import Device
from app.db.models.device_command import DeviceCommand
from tests.test_devices import CLOUD, IOT, WIFI, client, create, device_env, error


def edge(result):
    return {"X-Device-Key": result["device_key"]}


def beat(client, result, **body):
    response = client.post(f"/devices/{result['device']['id']}/heartbeat", headers=edge(result), json=body)
    assert response.status_code == 200, response.text
    return response.json()["commands"]


def test_delivery_duplicates_ack_and_isolation(client):
    result, other = create(client), create(client, name="Other")
    device_id = result["device"]["id"]
    path = f"/devices/{device_id}/commands"
    response = client.post(path, headers=IOT, json={"command": "reboot"})
    assert response.status_code == 202
    command_id = response.json()["command_id"]
    error(client.post(path, headers=IOT, json={"command": "reboot"}), 409)
    ack = f"{path}/{command_id}/ack"
    error(client.post(ack, headers=edge(result), json={"ok": True}), 409)
    error(client.post(ack, headers=edge(other), json={"ok": True}), 401)
    other_ack = f"/devices/{other['device']['id']}/commands/{command_id}/ack"
    error(client.post(other_ack, headers=edge(other), json={"ok": True}), 404)
    assert command_id in {row["command_id"] for row in beat(client, result)}
    assert beat(client, result) == []
    response = client.post(ack, headers=edge(result), json={"ok": False, "detail": "application failed"})
    assert response.status_code == 200 and response.json()["status"] == "failed"
    error(client.post(ack, headers=edge(result), json={"ok": True}), 409)
    assert client.post(path, headers=IOT, json={"command": "reboot"}).status_code == 202
    assert all("payload" not in row for row in client.get(path, headers=CLOUD).json())
    assert client.delete(f"/devices/{device_id}", headers=IOT).status_code == 204
    with postgres.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(DeviceCommand)
                              .where(DeviceCommand.device_id == device_id)) == 0


def test_expiry_and_wifi_supersession(client):
    result = create(client, wifi=WIFI)
    device_id = result["device"]["id"]
    first = next(row for row in beat(client, result) if row["command"] == "sync_config")["command_id"]
    response = client.put(f"/devices/{device_id}/wifi", headers=IOT,
                          json={"ssid": "New open network", "security": "open"})
    assert response.status_code == 202
    second = response.json()["command_id"]
    assert first != second
    error(client.post(f"/devices/{device_id}/commands/{first}/ack", headers=edge(result), json={"ok": True}), 409)
    assert client.get(f"/devices/{device_id}", headers=IOT).json()["status"] == "configuring"
    row = next(row for row in beat(client, result) if row["command_id"] == second)
    assert row["payload"]["wifi"]["ssid"] == "New open network"
    assert "password_enc" not in row["payload"]["wifi"]
    with postgres.session_scope() as session:
        session.get(DeviceCommand, second).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    error(client.post(f"/devices/{device_id}/commands/{second}/ack", headers=edge(result), json={"ok": True}), 409)
    history = client.get(f"/devices/{device_id}/commands?status=expired", headers=CLOUD).json()
    assert second in {row["command_id"] for row in history}


def test_wifi_update_gets_fresh_lifetime_and_latest_ack_wins(client):
    result = create(client, wifi=WIFI)
    device_id = result["device"]["id"]
    first = client.get(f"/devices/{device_id}/commands", headers=CLOUD).json()[0]["command_id"]
    with postgres.session_scope() as session:
        session.get(DeviceCommand, first).expires_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    response = client.put(f"/devices/{device_id}/wifi", headers=IOT, json=WIFI)
    second = response.json()["command_id"]
    assert second != first
    history = client.get(f"/devices/{device_id}/commands", headers=CLOUD).json()
    expiry = datetime.fromisoformat(next(row for row in history if row["command_id"] == second)["expires_at"].replace("Z", "+00:00"))
    assert expiry > datetime.now(timezone.utc) + timedelta(minutes=9)
    beat(client, result)
    response = client.post(f"/devices/{device_id}/commands/{second}/ack", headers=edge(result), json={"ok": True})
    assert response.status_code == 200 and response.json()["status"] == "acknowledged"
    error(client.post(f"/devices/{device_id}/commands/{first}/ack", headers=edge(result), json={"ok": False}), 409)
    assert client.get(f"/devices/{device_id}", headers=IOT).json()["status"] == "online"


def test_simulator_requires_live_client_and_valid_category(client, device_env):
    result = create(client)
    device_id = result["device"]["id"]
    path = f"/devices/{device_id}/commands"
    for command in ("start_route", "stop_route", "simulate_event"):
        payload = {"scenario": "emergency"} if command == "simulate_event" else {}
        error(client.post(path, headers=IOT, json={"command": command, "payload": payload}), 409)
    beat(client, result, lat=37.3, lon=-121.9, speed_kmh=20, heading=90)
    error(client.post(path, headers=IOT, json={"command": "simulate_event", "payload": {"scenario": "unknown"}}), 422)
    response = client.post(path, headers=IOT, json={"command": "simulate_event", "payload": {"category": "traffic"}})
    assert response.status_code == 202
    delivered = next(row for row in beat(client, result) if row["command"] == "simulate_event")
    assert delivered["payload"] == {"scenario": "traffic"}
    state = client.get("/simulator/10/state", headers=CLOUD)
    assert state.status_code == 200 and state.json()["online"]
    assert state.json()["position"]["lat"] == 37.3 and state.json()["events"]
    error(client.get("/simulator/10/state", headers={"X-Role": "owner"}), 403)
    error(client.get("/simulator/999/state", headers=CLOUD), 404)
    with postgres.session_scope() as session:
        session.get(Device, device_id).last_seen = datetime.now(timezone.utc) - timedelta(seconds=91)
    error(client.post(path, headers=IOT, json={"command": "start_route"}), 409)
    assert not client.get("/simulator/10/state", headers=CLOUD).json()["online"]


def test_frames_auth_size_type_and_simulator_link(client, device_env):
    camera = create(client, name="Camera", device_type="camera")
    mic = create(client)
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="JPEG")
    jpeg = output.getvalue()
    path = f"/devices/{camera['device']['id']}/frame"
    def post(data=jpeg, mime="image/jpeg", headers=None):
        return client.post(path, headers=edge(camera) if headers is None else headers,
                           files={"frame": ("frame.jpg", data, mime)})
    error(post(headers={}), 401)
    error(post(headers=edge(mic)), 401)
    error(post(b"bad"), 415)
    error(post(b"\xff\xd8\xff\xe0fake JPEG\xff\xd9"), 415)
    error(post(jpeg, "image/png"), 415)
    error(post(b"x" * (2 * 1024 * 1024 + 1)), 413)
    error(client.post(f"/devices/{mic['device']['id']}/frame", headers=edge(mic),
                      files={"frame": ("frame.jpg", jpeg, "image/jpeg")}), 403)
    assert post().status_code == 204
    assert device_env["objects"]["frames/10/latest.jpg"]["body"] == jpeg
    state = client.get("/simulator/10/state", headers=CLOUD).json()
    assert state["frame_url"].startswith("https://example.test/frames/10/latest.jpg")


def test_migration_upgrade_preserves_data_and_roundtrips(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite'}"
    env = {**os.environ, "DATABASE_URL": url}
    import sys
    def migrate(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND_DIR,
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout
    migrate("upgrade", "0001_initial_schema")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("INSERT INTO users(id,name,email,role,password_hash) VALUES(1,'Owner','a@test.dev','owner','hash')")
        connection.exec_driver_sql("INSERT INTO cars(id,owner_id,make,model) VALUES(1,1,'Toyota','Camry')")
        connection.exec_driver_sql("INSERT INTO devices(id,car_id,name,device_key_hash) VALUES(1,1,'Mic','hash')")
    migrate("upgrade", "head")
    assert migrate("heads").strip() == "0002_device_commands (head)"
    assert "device_commands" in inspect(engine).get_table_names()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("SELECT name FROM devices WHERE id=1").scalar() == "Mic"
        connection.exec_driver_sql("INSERT INTO device_commands(device_id,command,status,expires_at) VALUES(1,'reboot','queued','2030-01-01')")
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.exec_driver_sql("INSERT INTO device_commands(device_id,command,status,expires_at) VALUES(1,'reboot','queued','2030-01-01')")
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.exec_driver_sql("INSERT INTO device_commands(device_id,command,status,expires_at) VALUES(1,'unknown','queued','2030-01-01')")
        connection.exec_driver_sql("DELETE FROM devices WHERE id=1")
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM device_commands").scalar() == 0
    migrate("downgrade", "0001_initial_schema")
    assert "device_commands" not in inspect(engine).get_table_names()
    migrate("upgrade", "head")
    engine.dispose()
