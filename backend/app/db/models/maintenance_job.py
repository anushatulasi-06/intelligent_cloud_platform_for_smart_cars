import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, check_in
from app.db.models.constants import JOB_STATUSES, JOB_TYPES

# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in tests).
JsonB = JSON().with_variant(JSONB(), "postgresql")


class MaintenanceJob(Base):
    """Backup and retention runs (F14)."""

    __tablename__ = "maintenance_jobs"
    __table_args__ = (
        check_in("job_type", JOB_TYPES),
        check_in("status", JOB_STATUSES),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str | None] = mapped_column(String(12))
    status: Mapped[str | None] = mapped_column(String(10))
    params: Mapped[dict[str, Any] | None] = mapped_column(JsonB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonB)
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
