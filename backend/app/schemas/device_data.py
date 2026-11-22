"""C3 upload receipts and the staff processing monitor."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RecordStatus = Literal["pending", "processing", "processed", "failed"]


class UploadReceipt(BaseModel):
    record_id: str
    status: RecordStatus


class RecordDetail(UploadReceipt):
    car_id: int
    device_id: int
    clip_id: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    duration_s: float | None = None
    captured_at: datetime | None = None
    received_timestamp: datetime
    processed_at: datetime | None = None
    trigger: str | None = None
    lat: float | None = None
    lon: float | None = None
    processed: bool = False
    attempts: int = 0
    lease_until: datetime | None = None
    error: str | None = None
    alert_id: str | None = None
    s3_object_key: str


class RecordPage(BaseModel):
    items: list[RecordDetail]
    page: int
    page_size: int
    total: int


class HourCount(BaseModel):
    hour: datetime
    count: int


class RecordStats(BaseModel):
    counts: dict[str, int]
    per_hour: list[HourCount]
    avg_latency_s: float | None
    bytes_stored: int
