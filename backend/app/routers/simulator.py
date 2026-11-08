from fastapi import APIRouter, Depends, Path

from app.auth_stub import require_role
from app.services.device_service import simulator_state

router = APIRouter(prefix="/simulator", tags=["simulator"],
                   dependencies=[Depends(require_role("iot_staff", "cloud_staff"))])


@router.get("/{car_id}/state")
def state(car_id: int = Path(ge=1)):
    return simulator_state(car_id)
