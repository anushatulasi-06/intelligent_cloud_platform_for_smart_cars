"""C3 edge upload and staff record monitoring."""
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile

from app.auth_stub import require_role
from app.schemas.alerts import AudioLink
from app.schemas.device_data import RecordDetail, RecordPage, RecordStats, RecordStatus, UploadReceipt
from app.services.device_service import verify_device_key
from app.services.errors import NotFound, Unprocessable
from app.services.ingest_service import IngestService, MAX_AUDIO_BYTES

router = APIRouter(prefix="/device-data", tags=["device data"])
staff = require_role("iot_staff", "cloud_staff")


def _edge_device(device_id: Annotated[int, Form(gt=0)],
                 x_device_key: Annotated[str | None, Header()] = None) -> dict:
    try:
        return verify_device_key(device_id, x_device_key)
    except NotFound:
        raise HTTPException(401, "Invalid device key") from None


def _utc(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.post("", response_model=UploadReceipt, status_code=202)
def upload_audio(
    response: Response,
    device: Annotated[dict, Depends(_edge_device)],
    audio_file: Annotated[UploadFile, File()],
    car_id: Annotated[int, Form(gt=0)],
    captured_at: Annotated[datetime | None, Form()] = None,
    clip_id: Annotated[UUID | None, Form()] = None,
    trigger: Annotated[str | None, Form(max_length=100)] = None,
    lat: Annotated[float | None, Form(ge=-90, le=90, allow_inf_nan=False)] = None,
    lon: Annotated[float | None, Form(ge=-180, le=180, allow_inf_nan=False)] = None,
):
    service = IngestService()
    clip = str(clip_id) if clip_id else None
    existing = service.existing_clip(clip, device, car_id)
    if existing:
        response.status_code = 200
        return service.receipt(existing)
    if (lat is None) != (lon is None):
        raise Unprocessable("lat and lon must be supplied together")
    data = audio_file.file.read(MAX_AUDIO_BYTES + 1)
    receipt, created = service.upload(data, audio_file.content_type, device=device, car_id=car_id,
                                      clip_id=clip, captured_at=_utc(captured_at), trigger=trigger,
                                      lat=lat, lon=lon)
    response.status_code = 202 if created else 200
    return receipt


@router.get("", response_model=RecordPage, dependencies=[Depends(staff)])
def list_records(
    status: RecordStatus | None = None,
    car_id: int | None = Query(default=None, gt=0),
    device_id: int | None = Query(default=None, gt=0),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    from_, to = _utc(from_), _utc(to)
    if from_ and to and from_ > to:
        raise Unprocessable("from must be before to")
    return IngestService().list_records(status=status, car_id=car_id, device_id=device_id,
                                        from_=from_, to=to, page=page, page_size=page_size)


# Keep stats ahead of the UUID detail route.
@router.get("/stats", response_model=RecordStats, dependencies=[Depends(staff)])
def record_stats(window_hours: int = Query(default=12, ge=1, le=168)):
    return IngestService().stats(window_hours=window_hours)


@router.get("/{record_id}", response_model=RecordDetail, dependencies=[Depends(staff)])
def get_record(record_id: UUID):
    return IngestService().get(str(record_id))


@router.get("/{record_id}/audio", response_model=AudioLink, dependencies=[Depends(staff)])
def record_audio(record_id: UUID):
    return IngestService().audio(str(record_id))


@router.post("/{record_id}/retry", response_model=UploadReceipt, status_code=202,
             dependencies=[Depends(require_role("cloud_staff"))])
def retry_record(record_id: UUID):
    return IngestService().retry(str(record_id))
