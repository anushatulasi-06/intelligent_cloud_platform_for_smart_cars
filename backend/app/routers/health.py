"""GET /health – liveness check for the load balancer (public, no role needed).

200 {status: "ok", version} when PostgreSQL answers; 503 {status: "unavailable", version}
when it does not. MongoDB and S3 health is reported by GET /admin/db/health (C5), not here.
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import postgres

log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> JSONResponse:
    version = get_settings().APP_VERSION
    try:
        postgres.ping()
    except Exception:  # noqa: BLE001 – any failure means "not ready"; details go to the log only
        log.exception("health: PostgreSQL ping failed")
        return JSONResponse(status_code=503, content={"status": "unavailable", "version": version})
    return JSONResponse(status_code=200, content={"status": "ok", "version": version})
