"""Device inventory uses shared user auth; edge routes use the device's own key."""
from fastapi import APIRouter, Depends, File, Header, Path, Query, Response, UploadFile

from app.auth_stub import CurrentUser, require_role
from app.schemas.devices import (CommandAck, CommandCreate, CommandStatus, DeviceCreate, DeviceStatus,
                                 DeviceType, DeviceUpdate, Heartbeat, WifiConfig, DeviceOut, DevicePage,
                                 DeviceCreated, CommandQueued, CommandOut, HeartbeatOut, AckOut)
from app.services import device_service
from app.services.device_service import DeviceService

router = APIRouter(prefix="/devices", tags=["devices"])
viewer = require_role("owner", "iot_staff", "cloud_staff")
staff = require_role("iot_staff", "cloud_staff")
editor = require_role("iot_staff")


@router.get("", response_model=DevicePage)
def list_devices(user: CurrentUser = Depends(viewer), car_id: int | None = Query(default=None, ge=1),
                 status: DeviceStatus | None = None, device_type: DeviceType | None = None,
                 q: str | None = Query(default=None, max_length=60), page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100)):
    return DeviceService(user).list(car_id=car_id, status=status, device_type=device_type, q=q,
                                    page=page, page_size=page_size)


@router.post("", status_code=201, response_model=DeviceCreated)
def create_device(body: DeviceCreate, user: CurrentUser = Depends(editor)):
    return DeviceService(user).create(body)


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: int = Path(ge=1), user: CurrentUser = Depends(viewer)):
    return DeviceService(user).get(device_id)


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(body: DeviceUpdate, device_id: int = Path(ge=1), user: CurrentUser = Depends(editor)):
    return DeviceService(user).update(device_id, body)


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: int = Path(ge=1), user: CurrentUser = Depends(editor)):
    DeviceService(user).delete(device_id)
    return Response(status_code=204)


@router.put("/{device_id}/wifi", status_code=202, response_model=CommandQueued)
def update_wifi(body: WifiConfig, device_id: int = Path(ge=1), user: CurrentUser = Depends(editor)):
    return DeviceService(user).wifi(device_id, body)


@router.post("/{device_id}/commands", status_code=202, response_model=CommandQueued)
def queue_command(body: CommandCreate, device_id: int = Path(ge=1), user: CurrentUser = Depends(editor)):
    return DeviceService(user).enqueue(device_id, body.command, body.payload)


@router.get("/{device_id}/commands", response_model=list[CommandOut])
def command_history(device_id: int = Path(ge=1), user: CurrentUser = Depends(staff),
                    status: CommandStatus | None = None, limit: int = Query(default=20, ge=1, le=100)):
    return DeviceService(user).commands(device_id, status, limit)


@router.post("/{device_id}/heartbeat", response_model=HeartbeatOut)
def heartbeat(body: Heartbeat, device_id: int = Path(ge=1), key: str | None = Header(None, alias="X-Device-Key")):
    return device_service.heartbeat(device_id, key, body)


@router.post("/{device_id}/commands/{command_id}/ack", response_model=AckOut)
def acknowledge(body: CommandAck, device_id: int = Path(ge=1), command_id: int = Path(ge=1),
                key: str | None = Header(None, alias="X-Device-Key")):
    return device_service.acknowledge(device_id, command_id, key, body.ok, body.detail)


@router.post("/{device_id}/frame", status_code=204)
def upload_frame(device_id: int = Path(ge=1), frame: UploadFile = File(...),
                 key: str | None = Header(None, alias="X-Device-Key")):
    device_service.verify_device_key(device_id, key)
    data = frame.file.read(device_service.FRAME_MAX_BYTES + 1)
    device_service.store_frame(device_id, key, frame.content_type, data)
    return Response(status_code=204)
