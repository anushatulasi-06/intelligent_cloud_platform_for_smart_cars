from pathlib import Path as FilePath
from typing import Literal
import wave

from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse

from app.auth_stub import require_role
from app.services.device_service import simulator_state
from app.db import postgres
from app.db.repositories import CarRepo
from app.services.errors import Unavailable

SCENARIO_FILES = {
    "emergency": "1-54084-A-42.wav", "in_vehicle": "1-20133-A-39.wav",
    "human": "1-211527-A-20.wav", "traffic": "1-17124-A-43.wav",
    "environmental": "1-17367-A-10.wav",
}
SCENARIO_DIRECTORY = FilePath(__file__).resolve().parents[2] / "edge" / "demo_clips"
Scenario = Literal["emergency", "in_vehicle", "human", "traffic", "environmental"]

router = APIRouter(prefix="/simulator", tags=["simulator"],
                   dependencies=[Depends(require_role("iot_staff", "cloud_staff"))])


@router.get("/scenarios/{category}/audio", response_class=FileResponse)
def scenario_audio(category: Scenario):
    """Authenticated browser preview of a bundled, attributed scenario recording.

    Categories select fixed packaged files. No client-supplied path is opened.
    The edge still uploads its configured recording through the normal C3 path.
    """
    path = SCENARIO_DIRECTORY / SCENARIO_FILES[category]
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getnframes() == 0 or audio.getframerate() <= 0:
                raise ValueError("Empty recording")
    except (OSError, EOFError, ValueError, wave.Error):
        raise Unavailable("The bundled scenario recording is unavailable") from None
    return FileResponse(path, media_type="audio/wav", filename=f"scenario-{category}.wav",
                        content_disposition_type="inline", headers={
                            "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                            "Link": '<https://creativecommons.org/licenses/by-nc/3.0/>; rel="license"',
                        })


@router.get("/cars")
def cars():
    """Inventory choices for F11, including cars that have no devices yet."""
    with postgres.session_scope() as session:
        return [{"car_id": car.id, "make": car.make, "model": car.model, "plate": car.plate}
                for car in CarRepo(session).list_all()]


@router.get("/{car_id}/state")
def state(car_id: int = Path(ge=1)):
    return simulator_state(car_id)
