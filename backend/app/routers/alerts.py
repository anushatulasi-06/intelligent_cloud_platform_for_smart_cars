"""Alerts API (C4): owners see only their own cars' alerts (filtered in the Mongo query),
cloud staff see all, iot_staff gets 403. Errors are {"detail": "..."}."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request

from app.auth_stub import CurrentUser, require_role
from app.schemas.admin_config import ErrorOut
from app.schemas.alerts import (AlertDetail, AlertPage, AlertStatus, AlertStatusUpdate, AlertSummary, AudioLink,
                                Severity, SoundType)
from app.services.alert_service import AlertService

ERR = {"model": ErrorOut}
viewer = require_role("owner", "cloud_staff")

router = APIRouter(prefix="/alerts", tags=["alerts"], responses={401: ERR, 403: ERR})


def _utc(value: datetime | None) -> datetime | None:
    """Query datetimes without a timezone are taken as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.get("", response_model=AlertPage)
def list_alerts(
    user: CurrentUser = Depends(viewer),
    car_id: int | None = Query(default=None, ge=1),
    sound_type: SoundType | None = None,
    severity: Severity | None = None,
    status: AlertStatus | None = None,
    is_anomaly: bool | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Newest first. Owners: only their own cars, even when car_id names someone else's car."""
    return AlertService(user).list_alerts(
        page=page, page_size=page_size, car_id=car_id, sound_type=sound_type, severity=severity,
        status=status, is_anomaly=is_anomaly, from_=_utc(from_), to=_utc(to))


# Must be declared before /{alert_id}, or "summary" would be taken as an alert id.
@router.get("/summary", response_model=AlertSummary)
def alerts_summary(
    user: CurrentUser = Depends(viewer),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    car_id: int | None = Query(default=None, ge=1),
):
    return AlertService(user).summary(from_=_utc(from_), to=_utc(to), car_id=car_id)


@router.get("/{alert_id}", response_model=AlertDetail, responses={404: ERR})
def get_alert(alert_id: str, user: CurrentUser = Depends(viewer)):
    """404 for an unknown id and for another owner's alert (its existence is not revealed)."""
    return AlertService(user).get(alert_id)


@router.put("/{alert_id}", response_model=AlertDetail, responses={404: ERR, 409: ERR, 422: ERR})
def update_alert(alert_id: str, body: AlertStatusUpdate, user: CurrentUser = Depends(viewer)):
    """new -> acknowledged (owner of the car or cloud staff) -> resolved (cloud staff only)."""
    return AlertService(user).update_status(alert_id, body.status, body.note)


@router.get("/{alert_id}/audio", response_model=AudioLink, responses={404: ERR, 503: ERR})
def alert_audio(alert_id: str, request: Request, user: CurrentUser = Depends(viewer)):
    """Presigned S3 link to the clip, valid for 300 seconds. Dev: with LOCAL_AUDIO_DIR set, a link to
    this API's /local-audio/ route instead."""
    return AlertService(user).audio(alert_id, base_url=str(request.base_url))
