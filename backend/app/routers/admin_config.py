"""Config page API (C5): /admin/alert-types and /admin/models. cloud_staff only.

Every route commits its own transaction before returning, so a 200 means the change is saved.
Errors are {"detail": "..."} (see app.main error handlers).
"""
from fastapi import APIRouter, Depends, Path

from app.auth_stub import require_role
from app.db.postgres import session_scope
from app.schemas.admin_config import (AlertTypeOut, AlertTypeUpdate, ErrorOut, ModelActivated, ModelOut,
                                      ModelThresholdUpdate)
from app.services.alert_type_service import AlertTypeService
from app.services.model_service import ModelService

ERRORS = {code: {"model": ErrorOut} for code in (401, 403, 404, 409, 422, 500)}

router = APIRouter(prefix="/admin", tags=["config"], dependencies=[Depends(require_role("cloud_staff"))],
                   responses={401: ERRORS[401], 403: ERRORS[403]})


@router.get("/alert-types", response_model=list[AlertTypeOut])
def list_alert_types():
    with session_scope() as s:
        return [AlertTypeOut.model_validate(t) for t in AlertTypeService(s).list_types()]


@router.put("/alert-types/{alert_type}", response_model=AlertTypeOut,
            responses={404: ERRORS[404], 422: ERRORS[422]})
def update_alert_type(body: AlertTypeUpdate, alert_type: str = Path(max_length=16)):
    """Affects new alerts only; existing alerts keep their severity (BR-CF-3)."""
    with session_scope() as s:
        return AlertTypeOut.model_validate(AlertTypeService(s).update_type(alert_type, body.changes()))


@router.get("/models", response_model=list[ModelOut])
def list_models():
    with session_scope() as s:
        return [ModelOut.model_validate(m) for m in ModelService(s).list_models()]


@router.put("/models/{model_id}", response_model=ModelOut, responses={404: ERRORS[404], 422: ERRORS[422]})
def set_model_threshold(model_id: int, body: ModelThresholdUpdate):
    with session_scope() as s:
        return ModelOut.model_validate(ModelService(s).set_threshold(model_id, body.confidence_threshold))


@router.put("/models/{model_id}/activate", response_model=ModelActivated,
            responses={404: ERRORS[404], 409: ERRORS[409], 422: ERRORS[422], 500: ERRORS[500]})
def activate_model(model_id: int):
    """One transaction: deactivate all, activate this one; then reload the orchestrator."""
    return ModelService.activate(model_id)
