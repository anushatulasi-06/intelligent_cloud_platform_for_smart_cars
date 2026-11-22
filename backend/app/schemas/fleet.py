"""Request and response bodies for the map and tracking views (C1, F9 and F10)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MarkerStatus = Literal["online", "alert", "urgent", "offline"]
SoundType = Literal["emergency", "in_vehicle", "human", "traffic", "environmental"]


class MapCar(BaseModel):
    car_id: int
    name: str
    lat: float | None = None
    lon: float | None = None
    heading: float | None = None
    speed_kmh: float | None = None
    position_at: datetime | None = None
    status: MarkerStatus
    open_alerts: int
    top_severity: str | None = None


class FleetMap(BaseModel):
    cars: list[MapCar]


class TrackPoint(BaseModel):
    ts: datetime
    lat: float
    lon: float
    speed_kmh: float | None = None
    heading: float | None = None


class TrackAlert(BaseModel):
    id: str
    timestamp: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    sound_type: str
    severity: str | None = None
    status: str


class CarTrack(BaseModel):
    car_id: int
    points: list[TrackPoint]
    alerts: list[TrackAlert]
