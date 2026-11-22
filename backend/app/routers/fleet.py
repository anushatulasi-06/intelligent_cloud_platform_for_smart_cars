"""Map and tracking API (C1, F9 and F10). Owners see their own cars, cloud staff see all of
them; iot_staff is rejected with 403."""
from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query

from app.auth import CurrentUser, require_role
from app.schemas.admin_config import ErrorOut
from app.schemas.fleet import CarTrack, FleetMap, MarkerStatus, SoundType
from app.services.map_service import MAX_TRACK_POINTS, MapService, TrackService

ERR = {"model": ErrorOut}
viewer = require_role("owner", "cloud_staff")

router = APIRouter(tags=["map"], responses={401: ERR, 403: ERR})


@router.get("/fleet/map", response_model=FleetMap)
def fleet_map(status: MarkerStatus | None = None, sound_type: SoundType | None = None,
              car_id: int | None = Query(default=None, ge=1),
              user: CurrentUser = Depends(viewer)):
    """One marker per car, coloured by DT-MV-1. The page polls this every 5 seconds."""
    return MapService(user).fleet_positions(status=status, sound_type=sound_type, car_id=car_id)


@router.get("/cars/{car_id}/track", response_model=CarTrack, responses={404: ERR, 422: ERR})
def car_track(car_id: int = Path(ge=1),
              from_: datetime | None = Query(default=None, alias="from"),
              to: datetime | None = None,
              max_points: int = Query(default=MAX_TRACK_POINTS, ge=2, le=5000),
              user: CurrentUser = Depends(viewer)):
    """One car's route with the alerts that happened on it. Defaults to the last 24 hours."""
    return TrackService(user).get_track(car_id, from_, to, max_points)
