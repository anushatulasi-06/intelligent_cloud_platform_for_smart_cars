"""C2 inventory, configuration and edge authentication without external services."""
import base64
import hashlib
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import mongo, postgres, s3
from app.db.models import Car, Device, User
from app.main import create_app
from tests.auth_headers import bearer

IOT = bearer("iot_staff")
CLOUD = bearer("cloud_staff")
OWNER = bearer("owner", 1)
WIFI = {"ssid": "Car network", "security": "wpa2", "password": "car-password", "ip_mode": "dhcp"}


@pytest.fixture
def device_env(sql_db, monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["autosound_test"]
    monkeypatch.setattr(mongo, "get_db", lambda: db)
    objects = {}

    def put(key, body, content_type=None):
        objects[key] = {"body": body, "content_type": content_type, "modified_at": datetime.now(timezone.utc)}
        return key

    monkeypatch.setattr(s3, "put_object", put)
    monkeypatch.setattr(s3, "exists", lambda key: key in objects)
    monkeypatch.setattr(s3, "object_last_modified", lambda key: objects.get(key, {}).get("modified_at"))
    monkeypatch.setattr(s3, "presigned_url", lambda key, expires=300: f"https://example.test/{key}?ttl={expires}")
    monkeypatch.setattr(get_settings(), "WIFI_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with postgres.session_scope() as session:
        session.add_all([User(id=1, name="A", email="a@test.dev", role="owner", password_hash="h"),
                         User(id=2, name="B", email="b@test.dev", role="owner", password_hash="h")])
        session.flush()
        session.add_all([Car(id=10, owner_id=1, make="Toyota", model="Camry"),
                         Car(id=20, owner_id=2, make="Honda", model="Civic")])
    return {"db": db, "objects": objects}


@pytest.fixture
def client(device_env):
    settings = Settings(PROCESSING_WORKER_ENABLED=False, DEVICE_SWEEPER_ENABLED=False,
                        MONGO_ENSURE_INDEXES_ON_STARTUP=False, LOAD_MODEL_ON_STARTUP=False,
                        FAIL_INTERRUPTED_JOBS_ON_STARTUP=False)
    with TestClient(create_app(settings)) as c:
        yield c


def create(client, *, car_id=10, name="Front microphone", device_type="microphone", wifi=None):
    body = {"car_id": car_id, "name": name, "device_type": device_type}
    if wifi is not None:
        body["wifi"] = wifi
    response = client.post("/devices", headers=IOT, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def error(response, status):
    assert response.status_code == status, response.text
    assert set(response.json()) == {"detail"} and isinstance(response.json()["detail"], str)


def test_creation_hashes_key_encrypts_wifi_and_hides_secrets(client):
    result = create(client, wifi=WIFI)
    device_id, key = result["device"]["id"], result["device_key"]
    assert len(key) >= 32 and result["device"]["status"] == "registered"
    with postgres.session_scope() as session:
        stored = session.get(Device, device_id)
        assert stored.device_key_hash == hashlib.sha256(key.encode()).hexdigest()
        assert stored.wifi_password_enc and WIFI["password"].encode() not in stored.wifi_password_enc
        assert Fernet(get_settings().WIFI_ENCRYPTION_KEY.encode()).decrypt(stored.wifi_password_enc).decode() == WIFI["password"]
    for headers in (IOT, CLOUD, OWNER):
        response = client.get(f"/devices/{device_id}", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["wifi"]["ssid"] == WIFI["ssid"] and "password" not in body["wifi"]
        assert not {"device_key", "device_key_hash", "wifi_password_enc"}.intersection(body)
        assert key not in response.text and WIFI["password"] not in response.text
    response = client.post(f"/devices/{device_id}/heartbeat", headers={"X-Device-Key": key}, json={})
    assert response.status_code == 200 and WIFI["password"] not in response.text
    wifi = next(row for row in response.json()["commands"] if row["command"] == "sync_config")["payload"]["wifi"]
    delivery_key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    assert Fernet(delivery_key).decrypt(wifi["password_enc"].encode()).decode() == WIFI["password"]


def test_roles_owner_scoping_and_filtering(client):
    mine = create(client)
    theirs = create(client, car_id=20, device_type="camera")
    for headers in (IOT, CLOUD):
        assert client.get("/devices", headers=headers).json()["total"] == 2
    own_page = client.get("/devices", headers=OWNER).json()
    assert own_page["total"] == 1 and [row["id"] for row in own_page["items"]] == [mine["device"]["id"]]
    error(client.get("/devices?car_id=20", headers=OWNER), 403)
    error(client.get(f"/devices/{theirs['device']['id']}", headers=OWNER), 404)
    error(client.get("/devices"), 401)
    error(client.get(f"/devices/{mine['device']['id']}/commands", headers=OWNER), 403)
    filtered = client.get("/devices?device_type=camera&q=front&page_size=1", headers=CLOUD).json()
    assert filtered["total"] == 1 and filtered["items"][0]["car_id"] == 20
    error(client.get("/devices?page_size=101", headers=CLOUD), 422)


def test_writes_require_iot_role(client):
    device_id = create(client)["device"]["id"]
    routes = [("POST", "/devices", {"car_id": 10, "name": "Other", "device_type": "camera"}),
              ("PUT", f"/devices/{device_id}", {"name": "Renamed"}),
              ("DELETE", f"/devices/{device_id}", None),
              ("PUT", f"/devices/{device_id}/wifi", WIFI),
              ("POST", f"/devices/{device_id}/commands", {"command": "reboot"})]
    for method, path, body in routes:
        for headers in (OWNER, CLOUD):
            error(client.request(method, path, headers=headers, json=body), 403)
        error(client.request(method, path, json=body), 401)


def test_duplicate_names_and_moves(client):
    first = create(client)["device"]
    error(client.post("/devices", headers=IOT,
                      json={"car_id": 10, "name": first["name"], "device_type": "camera"}), 409)
    create(client, car_id=20, name="Camera", device_type="camera")
    error(client.put(f"/devices/{first['id']}", headers=IOT, json={"car_id": 20, "name": "Camera"}), 409)
    response = client.put(f"/devices/{first['id']}", headers=IOT, json={"car_id": 20, "name": "Rear mic"})
    assert response.status_code == 200 and response.json()["car_id"] == 20
    error(client.put(f"/devices/{first['id']}", headers=IOT, json={"car_id": 999}), 404)
    error(client.post("/devices", headers=IOT, json={"car_id": 999, "name": "Missing", "device_type": "camera"}), 404)


@pytest.mark.parametrize("body", [{"name": ""}, {"name": " "}, {"name": "n" * 61},
                                      {"device_type": "speaker"}, {"car_id": 0}, {"car_id": None},
                                      {"name": None}, {"device_type": None}])
def test_device_validation(client, body):
    device_id = create(client)["device"]["id"]
    error(client.put(f"/devices/{device_id}", headers=IOT, json=body), 422)


@pytest.mark.parametrize("wifi", [
    {"ssid": "", "security": "open"}, {"ssid": "é" * 17, "security": "open"},
    {"ssid": "Network", "security": "wep"}, {"ssid": "Network", "security": "open", "password": "password"},
    {"ssid": "Network", "security": "wpa2"}, {"ssid": "Network", "security": "wpa3", "password": "short"},
    {"ssid": "Network", "security": "wpa2", "password": "x" * 64},
    {"ssid": "Network", "security": "open", "ip_mode": "automatic"},
])
def test_wifi_validation_on_create_and_update(client, wifi):
    error(client.post("/devices", headers=IOT,
                      json={"car_id": 10, "name": "Invalid wifi", "device_type": "camera", "wifi": wifi}), 422)
    device_id = create(client)["device"]["id"]
    error(client.put(f"/devices/{device_id}/wifi", headers=IOT, json=wifi), 422)


def test_heartbeat_authentication_and_position(client, device_env):
    result = create(client)
    device_id, key = result["device"]["id"], result["device_key"]
    path = f"/devices/{device_id}/heartbeat"
    error(client.post(path, headers=IOT, json={}), 401)
    error(client.post(path, headers={"X-Device-Key": "bad"}, json={}), 401)
    response = client.post(path, headers={"X-Device-Key": key},
                           json={"firmware": "1.0", "rssi": -55, "lat": 37.3, "lon": -121.9,
                                 "speed_kmh": 32, "heading": 90})
    assert response.status_code == 200 and response.json()["server_time"]
    public = client.get(f"/devices/{device_id}", headers=IOT).json()
    assert public["status"] == "online" and public["firmware"] == "1.0" and public["last_seen"]
    position = device_env["db"]["car_positions"].find_one({"car_id": 10})
    assert position["lat"] == 37.3 and position["lon"] == -121.9
    with postgres.session_scope() as session:
        car = session.get(Car, 10)
        assert car.last_lat == 37.3 and car.last_lon == -121.9 and car.last_position_at
    error(client.post(path, headers={"X-Device-Key": key}, json={"lat": 37.3}), 422)
    error(client.post(path, headers={"X-Device-Key": key}, json={"lat": 91, "lon": 1}), 422)


def test_sweeper_and_deletion(client):
    from app.services.device_service import sweep_devices

    stale = create(client, name="Stale")["device"]["id"]
    registered = create(client, name="Registered")["device"]["id"]
    now = datetime.now(timezone.utc)
    with postgres.session_scope() as session:
        session.get(Device, stale).last_seen = now - timedelta(seconds=100)
        session.get(Device, stale).status = "online"
    sweep_devices(now=now)
    assert client.get(f"/devices/{stale}", headers=IOT).json()["status"] == "offline"
    assert client.get(f"/devices/{registered}", headers=IOT).json()["status"] == "registered"
    assert client.delete(f"/devices/{stale}", headers=IOT).status_code == 204
    error(client.get(f"/devices/{stale}", headers=IOT), 404)
    error(client.post(f"/devices/{stale}/heartbeat", headers={"X-Device-Key": "anything"}, json={}), 404)
    error(client.delete(f"/devices/{stale}", headers=IOT), 404)


@pytest.mark.parametrize("store", ["postgres", "mongo"])
def test_store_outages_keep_error_contract(client, monkeypatch, store):
    from pymongo.errors import ServerSelectionTimeoutError
    from sqlalchemy.exc import OperationalError
    from app.db.repositories import DeviceRepo, RecordRepo

    def fail(*args, **kwargs):
        if store == "postgres":
            raise OperationalError("query", {}, RuntimeError("private connection detail"))
        raise ServerSelectionTimeoutError("private connection detail")
    repo, path = (DeviceRepo, "/devices") if store == "postgres" else (RecordRepo, "/device-data")
    monkeypatch.setattr(repo, "search", fail)
    response = client.get(path, headers=IOT)
    error(response, 503)
    assert response.json() == {"detail": "Database temporarily unavailable"}
    assert "private" not in response.text
