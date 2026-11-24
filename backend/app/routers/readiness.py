"""Authenticated readiness is separate from the inexpensive public /health probe."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth import require_role
from app.services.readiness_service import readiness

router = APIRouter(tags=["health"])


@router.get("/admin/readiness", dependencies=[Depends(require_role("cloud_staff"))])
def component_readiness(request: Request):
    """Read-only diagnostics; managed-service reads can still be metered."""
    result = readiness(getattr(request.app.state, "worker_runtime", None))
    return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503,
                        headers={"Cache-Control": "no-store"})
