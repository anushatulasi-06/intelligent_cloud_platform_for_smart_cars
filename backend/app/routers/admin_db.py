"""Database tab API (C5, F14): /admin/db/*. cloud_staff only. Errors are {"detail": "..."}."""
from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.auth_stub import CurrentUser, require_role
from app.schemas.admin_config import ErrorOut
from app.schemas.admin_db import (BackupOut, BackupQueued, DbHealthOut, DbStatsOut, RetentionRequest,
                                  RetentionResult)
from app.services import maintenance_service as svc

ERR = {"model": ErrorOut}
staff = require_role("cloud_staff")

router = APIRouter(prefix="/admin/db", tags=["database"], dependencies=[Depends(staff)],
                   responses={401: ERR, 403: ERR})


@router.get("/health", response_model=DbHealthOut)
def db_health():
    """Always 200: a store that cannot be reached is reported as "down", not raised."""
    return svc.health()


@router.get("/stats", response_model=DbStatsOut)
def db_stats():
    return svc.stats()


@router.post("/backup", response_model=BackupQueued, status_code=status.HTTP_202_ACCEPTED,
             responses={409: ERR})
def start_backup(background: BackgroundTasks, user: CurrentUser = Depends(staff)):
    """Queue a backup (409 if one is queued or running); the dump runs after the response."""
    job_id = svc.queue_backup(user.id)
    background.add_task(svc.run_backup, job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/backups", response_model=list[BackupOut])
def list_backups(limit: int = Query(default=20, ge=1, le=100)):
    """Newest first (queued jobs, which have not started yet, come first)."""
    return svc.list_backups(limit)


@router.post("/retention/run", response_model=RetentionResult, responses={422: ERR, 500: ERR})
def run_retention(body: RetentionRequest, user: CurrentUser = Depends(staff)):
    """dry_run (default) only counts. A real run archives to S3 first and deletes nothing if that fails."""
    return svc.run_retention(body.collection, body.older_than_days, body.dry_run, user.id)
