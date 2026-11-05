"""Request and response bodies for /admin/db/* (C5 Database tab, F14)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.maintenance_service import RETENTION_COLLECTIONS, RETENTION_MIN_DAYS

HealthStatus = Literal["healthy", "degraded", "down"]


class PostgresHealth(BaseModel):
    status: HealthStatus
    latency_ms: float | None
    pool_in_use: int | None
    pool_size: int | None


class StoreHealth(BaseModel):
    status: HealthStatus
    latency_ms: float | None


class DbHealthOut(BaseModel):
    postgres: PostgresHealth
    mongodb: StoreHealth
    s3: StoreHealth
    checked_at: datetime


class StoreStats(BaseModel):
    name: str
    engine: Literal["postgresql", "mongodb", "s3"]
    count: int | None
    size_bytes: int | None
    oldest: datetime | None


class DbStatsOut(BaseModel):
    stores: list[StoreStats]


class BackupQueued(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"


class BackupOut(BaseModel):
    job_id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    size_bytes: int | None
    error: str | None


class RetentionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection: Literal[RETENTION_COLLECTIONS]  # type: ignore[valid-type]
    older_than_days: int = Field(ge=RETENTION_MIN_DAYS, strict=True)
    dry_run: bool = True


class RetentionResult(BaseModel):
    candidates: int
    deleted: int
    archive_key: str | None = None
