"""C2 device inventory and edge control. All store access goes through repositories."""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from io import BytesIO

from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError

from app.auth_stub import CurrentUser
from app.config import get_settings
from app.db import postgres, s3
from app.db.models import Device
from app.db.repositories import AlertRepo, CarRepo, DeviceRepo, PositionRepo
from app.db.repositories.command_repo import CommandRepo
from app.schemas.devices import DeviceCreate, DeviceUpdate, Heartbeat, WifiConfig
from app.services.errors import Conflict, Forbidden, NotFound, ServiceError, Unavailable

FRAME_MAX_BYTES = 2 * 1024 * 1024


class Unauthorized(ServiceError):
    status_code = 401


class FrameTooLarge(ServiceError):
    status_code = 413


class UnsupportedFrame(ServiceError):
    status_code = 415


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _cipher():
    key = get_settings().WIFI_ENCRYPTION_KEY
    if not key:
        raise Unavailable("Set WIFI_ENCRYPTION_KEY before configuring password-protected Wi-Fi")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise Unavailable("WIFI_ENCRYPTION_KEY must be a valid Fernet key") from None


def _verify(device, key):
    if not key or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), device.device_key_hash):
        raise Unauthorized("Invalid device key")


def verify_device_key(device_id: int, key: str | None) -> dict:
    with postgres.session_scope() as session:
        device = DeviceRepo(session).get(device_id)
        if device is None:
            raise NotFound("Device not found")
        _verify(device, key)
        return {"id": device.id, "car_id": device.car_id, "device_type": device.device_type}


def _public(device):
    return {"id": device.id, "car_id": device.car_id, "name": device.name, "device_type": device.device_type,
            "status": device.status, "firmware": device.firmware, "last_seen": _utc(device.last_seen),
            "created_at": _utc(device.created_at), "updated_at": _utc(device.updated_at),
            "wifi": {"ssid": device.wifi_ssid, "security": device.wifi_security, "ip_mode": device.ip_mode}
            if device.wifi_ssid is not None else None}


def _command(row):
    # Payloads are for the edge client only; staff history never exposes credentials.
    return {"command_id": row.id, "command": row.command, "status": row.status,
            "created_at": _utc(row.created_at), "delivered_at": _utc(row.delivered_at),
            "acked_at": _utc(row.acked_at), "expires_at": _utc(row.expires_at)}


def _wifi_fields(wifi):
    return {"wifi_ssid": wifi.ssid, "wifi_security": wifi.security, "ip_mode": wifi.ip_mode,
            "wifi_password_enc": _cipher().encrypt(wifi.password.encode()) if wifi.password else None}


def _require_iot(user):
    if user.role != "iot_staff":
        raise Forbidden("Role not allowed")


class DeviceService:
    def __init__(self, user: CurrentUser):
        self.user = user

    def _get(self, repo, device_id, *, locked=False):
        if self.user.role == "owner":
            device = repo.get_for_owner(device_id, self.user.id)
        else:
            device = repo.get_locked(device_id) if locked else repo.get(device_id)
        if device is None:
            raise NotFound("Device not found")
        return device

    def list(self, **filters):
        with postgres.session_scope() as session:
            if self.user.role == "owner":
                if filters.get("car_id") and not CarRepo(session).is_owned_by(filters["car_id"], self.user.id):
                    raise Forbidden("Car is not owned by this user")
                filters["owner_id"] = self.user.id
            result = DeviceRepo(session).search(**filters)
            result["items"] = [_public(device) for device in result["items"]]
            return result

    def get(self, device_id):
        with postgres.session_scope() as session:
            return _public(self._get(DeviceRepo(session), device_id))

    def create(self, body: DeviceCreate):
        _require_iot(self.user)
        key = secrets.token_urlsafe(32)
        try:
            with postgres.session_scope() as session:
                if CarRepo(session).get(body.car_id) is None:
                    raise NotFound("Car not found")
                fields = _wifi_fields(body.wifi) if body.wifi else {}
                device = DeviceRepo(session).add(Device(car_id=body.car_id, name=body.name,
                    device_type=body.device_type, device_key_hash=hashlib.sha256(key.encode()).hexdigest(), **fields))
                CommandRepo(session).enqueue(device.id, "sync_config", {}, _now())
                result = {"device": _public(device), "device_key": key}
            return result
        except IntegrityError:
            raise Conflict("Duplicate device name on this car") from None

    def update(self, device_id, body: DeviceUpdate):
        _require_iot(self.user)
        try:
            with postgres.session_scope() as session:
                repo = DeviceRepo(session)
                self._get(repo, device_id, locked=True)
                if body.car_id and CarRepo(session).get(body.car_id) is None:
                    raise NotFound("Car not found")
                return _public(repo.update(device_id, body.model_dump(exclude_unset=True)))
        except IntegrityError:
            raise Conflict("Duplicate device name on this car") from None

    def delete(self, device_id):
        _require_iot(self.user)
        with postgres.session_scope() as session:
            repo = DeviceRepo(session)
            self._get(repo, device_id, locked=True)
            repo.delete(device_id)

    def wifi(self, device_id, body: WifiConfig):
        _require_iot(self.user)
        with postgres.session_scope() as session:
            devices, commands = DeviceRepo(session), CommandRepo(session)
            self._get(devices, device_id, locked=True)
            devices.update(device_id, {**_wifi_fields(body), "status": "configuring"})
            commands.supersede_config(device_id)
            command = commands.enqueue(device_id, "sync_config", {}, _now())
            return {"command_id": command.id, "status": "queued"}

    def enqueue(self, device_id, command, payload):
        _require_iot(self.user)
        try:
            with postgres.session_scope() as session:
                device = self._get(DeviceRepo(session), device_id, locked=True)
                repo = CommandRepo(session)
                now = _now()
                if command in ("start_route", "stop_route", "simulate_event") and (
                    device.last_seen is None or _utc(device.last_seen) < now - timedelta(seconds=90)
                ):
                    raise Conflict("Simulator client is offline")
                repo.expire_stale(now)
                if repo.queued(device_id, command):
                    raise Conflict("Same command already queued")
                row = repo.enqueue(device_id, command, {} if command == "sync_config" else payload, now)
                if command == "sync_config" and device.wifi_ssid is not None:
                    DeviceRepo(session).update(device_id, {"status": "configuring"})
                return {"command_id": row.id, "status": "queued"}
        except IntegrityError:
            raise Conflict("Same command already queued") from None

    def commands(self, device_id, status=None, limit=20):
        with postgres.session_scope() as session:
            self._get(DeviceRepo(session), device_id)
            repo = CommandRepo(session)
            repo.expire_stale(_now())
            return [_command(row) for row in repo.history(device_id, status, limit)]


def heartbeat(device_id: int, key: str | None, body: Heartbeat):
    now = _now()
    with postgres.session_scope() as session:
        devices = DeviceRepo(session)
        device = devices.get_locked(device_id)
        if device is None:
            raise NotFound("Device not found")
        _verify(device, key)
        commands = CommandRepo(session)
        configuring = device.wifi_ssid is not None and commands.has_open(device_id, "sync_config", now)
        devices.update(device_id, {"last_seen": now, "status": "configuring" if configuring else "online",
                                  **({"firmware": body.firmware} if body.firmware is not None else {})})
        if body.lat is not None:
            PositionRepo().add_position(device.car_id, now, body.lat, body.lon, body.speed_kmh, body.heading, body.town)
            CarRepo(session).update_position(device.car_id, body.lat, body.lon, now)
        pending = commands.pending_for(device_id, now)
        result = []
        for row in pending:
            payload = row.payload or {}
            if row.command == "sync_config" and device.wifi_ssid is not None:
                wifi = _public(device)["wifi"]
                if device.wifi_password_enc:
                    try:
                        password = _cipher().decrypt(device.wifi_password_enc)
                    except InvalidToken:
                        raise Unavailable("Wi-Fi credentials cannot be decrypted with the configured key") from None
                    # Protect the password in the edge response too. See the README edge contract.
                    delivery_key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
                    wifi["password_enc"] = Fernet(delivery_key).encrypt(password).decode()
                payload = {"wifi": wifi}
            result.append({"command_id": row.id, "command": row.command, "payload": payload})
        return {"server_time": now, "commands": result}


def acknowledge(device_id, command_id, key, ok, detail):
    now = _now()
    result = None
    with postgres.session_scope() as session:
        device = DeviceRepo(session).get_locked(device_id)
        if device is None:
            raise NotFound("Device not found")
        _verify(device, key)
        repo = CommandRepo(session)
        repo.expire_stale(now)
        previous = repo.get(command_id)
        if previous is None or previous.device_id != device_id:
            raise NotFound("Command not found")
        row = repo.acknowledge(device_id, command_id, ok, None, now)
        if row is not None:
            # Device-provided detail can contain credentials. Store only a generic failure reason.
            if not ok:
                repo.update(command_id, {"detail": "Device could not apply command"})
            if row.command == "sync_config":
                still_configuring = not ok or repo.has_open(device_id, "sync_config", now)
                DeviceRepo(session).update(device_id, {"status": "configuring" if still_configuring else "online"})
            result = {"status": row.status}
    if result is None:
        # Commit expiry before returning a conflict, rather than rolling it back with the error.
        raise Conflict("Command is not awaiting acknowledgement")
    return result


def store_frame(device_id, key, content_type, data):
    device = verify_device_key(device_id, key)
    if device["device_type"] != "camera":
        raise Forbidden("Frames require a camera device")
    if len(data) > FRAME_MAX_BYTES:
        raise FrameTooLarge("Frame exceeds 2 MB")
    if content_type != "image/jpeg" or not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise UnsupportedFrame("Frame must be a JPEG")
    try:
        with Image.open(BytesIO(data)) as frame:
            if frame.format != "JPEG":
                raise UnsupportedFrame("Frame must be a JPEG")
            frame.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise UnsupportedFrame("Frame must be a valid JPEG") from None
    try:
        s3.put_object(f"frames/{device['car_id']}/latest.jpg", data, content_type="image/jpeg")
    except (BotoCoreError, ClientError):
        raise Unavailable("Frame storage unavailable") from None


def simulator_state(car_id):
    now = _now()
    with postgres.session_scope() as session:
        if CarRepo(session).get(car_id) is None:
            raise NotFound("Car not found")
        devices = DeviceRepo(session).list_for_car(car_id)
        recent_devices = [device for device in devices
                          if device.last_seen and _utc(device.last_seen) >= now - timedelta(seconds=90)]
        online = bool(recent_devices)
        edge_mode = ("local_demo" if any((device.firmware or "").startswith("local-demo") for device in recent_devices)
                     else "carla" if any((device.firmware or "").startswith("carla") for device in recent_devices)
                     else "edge")
        audio_source = ("recordings" if any(device.firmware == "local-demo-real-v1" for device in recent_devices)
                        else "synthetic" if any(device.firmware == "local-demo-synth-v1" for device in recent_devices)
                        else None)
        camera_seen = max((_utc(device.last_seen) for device in devices
                           if device.device_type == "camera" and device.last_seen), default=None)
        repo = CommandRepo(session)
        repo.expire_stale(now)
        events = [{"command_id": row.id, "device_id": row.device_id,
                   "ts": _utc(row.created_at), "command": row.command, "status": row.status,
                   "detail": row.detail}
                  for row in repo.for_car(car_id)]
    position = PositionRepo().latest(car_id)
    alerts = AlertRepo().search(car_id=car_id, page_size=1)["items"]
    last_alert = {k: alerts[0].get(k) for k in ("id", "sound_type", "confidence", "severity")} if alerts else None
    frame_url = None
    frame_at = None
    try:
        frame_key = f"frames/{car_id}/latest.jpg"
        frame_at = _utc(s3.object_last_modified(frame_key))
        if frame_at is not None:
            frame_url = s3.presigned_url(frame_key)
    except (BotoCoreError, ClientError):
        pass  # A missing camera or unavailable S3 must not hide telemetry and commands.
    return {"car_id": car_id, "online": online, "edge_mode": edge_mode, "audio_source": audio_source,
            "camera_last_seen": camera_seen,
            "position": {k: position.get(k) for k in ("lat", "lon", "speed_kmh", "heading", "ts", "town")} if position else None,
            "frame_url": frame_url, "frame_at": frame_at, "events": events, "last_alert": last_alert}


def sweep_devices(now=None):
    now = now or _now()
    with postgres.session_scope() as session:
        count = DeviceRepo(session).sweep_offline(now - timedelta(seconds=90))
        CommandRepo(session).expire_stale(now)
        return count
