"""Request and response bodies for the dashboards (C1, F2 and F6)."""
from typing import Literal

from pydantic import BaseModel

from app.schemas.alerts import AlertListItem

GroupBy = Literal["sound_type", "severity", "status", "car", "day"]


class OwnerCar(BaseModel):
    id: int
    make: str
    model: str
    devices_total: int
    devices_online: int
    status: str


class OwnerCounts(BaseModel):
    alerts_today: int
    unresolved: int


class OwnerDashboard(BaseModel):
    cars: list[OwnerCar]
    counts: OwnerCounts
    recent_alerts: list[AlertListItem]


class DeviceCounts(BaseModel):
    registered: int = 0
    online: int = 0
    offline: int = 0
    configuring: int = 0


class DayCount(BaseModel):
    date: str
    count: int


class FleetSummary(BaseModel):
    total_cars: int
    devices: DeviceCounts
    alerts_today: int
    anomalies_today: int
    alerts_by_type: dict[str, int]
    alerts_per_day: list[DayCount]


class ReportRow(BaseModel):
    key: str
    count: int
    anomalies: int
    avg_confidence: float | None


class AlertReport(BaseModel):
    group_by: GroupBy
    rows: list[ReportRow]
