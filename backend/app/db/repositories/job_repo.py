"""maintenance_jobs (PostgreSQL): backup and retention runs."""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text, update

from app.db.models import MaintenanceJob
from app.db.repositories.base import SqlRepo


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobRepo(SqlRepo[MaintenanceJob]):
    model = MaintenanceJob
    time_column = "started_at"

    def create(self, job_type: str, params: dict[str, Any] | None = None,
               created_by: int | None = None) -> MaintenanceJob:
        return self.add(MaintenanceJob(id=uuid.uuid4(), job_type=job_type, status="queued",
                                       params=params or {}, created_by=created_by))

    def has_unfinished(self, job_type: str) -> bool:
        """POST /admin/db/backup returns 409 while one is queued or running."""
        return self.session.scalar(select(MaintenanceJob.id).where(
            MaintenanceJob.job_type == job_type,
            MaintenanceJob.status.in_(("queued", "running"))).limit(1)) is not None

    def lock_job_type(self, job_type: str) -> None:
        """Serialize "check then create" for one job type until this transaction ends, so two
        simultaneous POST /admin/db/backup calls cannot both start a job (PostgreSQL advisory lock;
        a no-op on other databases)."""
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"maintenance:{job_type}"})

    def fail_unfinished(self, reason: str) -> int:
        """Mark every queued/running job failed. Used at startup: background tasks run inside the API
        process, so a job left unfinished by a restart can never complete."""
        result = self.session.execute(
            update(MaintenanceJob).where(MaintenanceJob.status.in_(("queued", "running")))
            .values(status="failed", finished_at=_now(), error=reason[:500]))
        return result.rowcount or 0

    def list_recent(self, job_type: str | None = None, limit: int = 10) -> list[MaintenanceJob]:
        query = select(MaintenanceJob)
        if job_type:
            query = query.where(MaintenanceJob.job_type == job_type)
        # queued jobs have no started_at yet; show them first
        query = query.order_by(MaintenanceJob.started_at.desc().nulls_first()).limit(limit)
        return list(self.session.scalars(query))

    def mark_running(self, job_id: uuid.UUID) -> MaintenanceJob | None:
        return self.update(job_id, {"status": "running", "started_at": _now()})

    def mark_succeeded(self, job_id: uuid.UUID, result: dict[str, Any] | None = None) -> MaintenanceJob | None:
        return self.update(job_id, {"status": "succeeded", "finished_at": _now(), "result": result or {}})

    def mark_failed(self, job_id: uuid.UUID, error: str) -> MaintenanceJob | None:
        return self.update(job_id, {"status": "failed", "finished_at": _now(), "error": error[:500]})
