from fastapi import APIRouter, Depends, Path

from app.auth_stub import require_role
from app.services.device_service import simulator_state
from app.db import postgres
from app.db.repositories import CarRepo

router = APIRouter(prefix="/simulator", tags=["simulator"],
                   dependencies=[Depends(require_role("iot_staff", "cloud_staff"))])


@router.get("/cars")
def cars():
    """Inventory choices for F11, including cars that have no devices yet."""
    with postgres.session_scope() as session:
        return [{"car_id": car.id, "make": car.make, "model": car.model, "plate": car.plate}
                for car in CarRepo(session).list_all()]


@router.get("/{car_id}/state")
def state(car_id: int = Path(ge=1)):
    return simulator_state(car_id)
