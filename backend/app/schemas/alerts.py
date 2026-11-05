"""Request and response bodies for /alerts (C4, F12/F7)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SoundType = Literal["emergency", "in_vehicle", "human", "traffic", "environmental"]
Severity = Literal["low", "medium", "high", "critical"]
AlertStatus = Literal["new", "acknowledged", "resolved"]


class AlertListItem(BaseModel):
    id: str
    record_id: str | None = None
    car_id: int
    device_id: int | None = None
    sound_type: str
    confidence: float | None = None
    is_anomaly: bool = False
    severity: str | None = None
    status: str
    timestamp: datetime | None = None


class AlertPage(BaseModel):
    items: list[AlertListItem]
    page: int
    page_size: int
    total: int


class AlertDetail(AlertListItem):
    probs: dict[str, float] | None = None
    model_version: str | None = None
    lat: float | None = None
    lon: float | None = None
    s3_object_key: str | None = None
    acknowledged_by: int | None = None
    acknowledged_at: datetime | None = None
    resolved_by: int | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None


class AlertStatusUpdate(BaseModel):
    """PUT /alerts/{id}. Only new -> acknowledged -> resolved; the note is stored when resolving."""
    model_config = ConfigDict(extra="forbid")

    status: AlertStatus
    note: str | None = Field(default=None, max_length=500)


class AlertSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    by_severity: dict[str, int]
    by_type: dict[str, int]
    anomalies: int


class AudioLink(BaseModel):
    url: str
    expires_in: int
