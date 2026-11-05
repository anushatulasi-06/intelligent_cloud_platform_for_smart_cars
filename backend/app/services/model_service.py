"""Model versions (F8b): list, threshold, and activation (BR-CF-1, DT-CF-1).

Activation runs in ONE transaction: deactivate every row, activate the chosen one. Only after that
commit is the orchestrator asked to reload. If anything fails, the previously active model stays
active:
  - a failure inside the transaction rolls it back;
  - a failure in reload_model() (after commit) re-activates the previous model.
"""
import logging
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.db import postgres
from app.db.models import ModelVersion
from app.db.repositories import ModelRepo
from app.services.orchestrator import MLOrchestrator, get_orchestrator
from app.services.errors import Conflict, NotFound, ServiceError, ServiceFailure, Unprocessable

log = logging.getLogger(__name__)

WEIGHTS_FILE = "model_weights.pt"


def weights_path(artifact_path: str) -> Path:
    """<artifact_path>/model_weights.pt. Relative paths resolve from backend/, like MODEL_DIR."""
    base = Path(artifact_path)
    return (base if base.is_absolute() else BACKEND_DIR / base) / WEIGHTS_FILE


class ModelService:
    def __init__(self, session: Session):
        self.repo = ModelRepo(session)

    def list_models(self) -> list[ModelVersion]:
        return self.repo.list_all()

    def set_threshold(self, model_id: int, threshold: float) -> ModelVersion:
        if self.repo.get(model_id) is None:
            raise NotFound(f"Model {model_id} not found")
        try:
            return self.repo.set_threshold(model_id, threshold)
        except ValueError as exc:
            raise Unprocessable(str(exc)) from None

    @staticmethod
    def activate(model_id: int, orchestrator: MLOrchestrator | None = None) -> dict:
        """PUT /admin/models/{id}/activate. Opens its own transaction so the commit happens before reload."""
        orchestrator = orchestrator or get_orchestrator()
        try:
            with postgres.session_scope() as session:
                repo = ModelRepo(session)
                model = repo.get(model_id)
                if model is None:
                    raise NotFound(f"Model {model_id} not found")                          # DT-CF-1 R2
                if model.is_active:
                    raise Conflict(f"{model.version_label} is already active")             # R3
                if not model.artifact_path or not weights_path(model.artifact_path).is_file():
                    raise Unprocessable(f"Weights file missing for {model.version_label}: "
                                        f"{model.artifact_path}/{WEIGHTS_FILE}")           # R4
                previous = repo.get_active()
                previous_id = previous.id if previous else None
                previous_label = previous.version_label if previous else None
                repo.activate(model_id)            # deactivate all, then activate this one
                result = {"id": model.id, "version_label": model.version_label, "is_active": True}
        except ServiceError:
            raise
        except IntegrityError:
            # The partial unique index rejected a second active row (a concurrent activation).
            raise Conflict("Another model was activated at the same time; try again") from None
        except Exception:
            log.exception("Model activation failed; transaction rolled back")
            raise ServiceFailure("Activation failed; the previous model is still active") from None

        # Committed. Now load the new weights (R5); undo the switch if that fails.
        try:
            orchestrator.reload_model()
        except Exception:
            log.exception("reload_model failed for %s; restoring %s", result["version_label"], previous_label)
            ModelService._restore(previous_id, orchestrator)
            kept = previous_label or "no model"
            raise ServiceFailure(f"Could not load {result['version_label']}; {kept} is still active") from None
        return result

    @staticmethod
    def _restore(previous_id: int | None, orchestrator: MLOrchestrator) -> None:
        with postgres.session_scope() as session:
            repo = ModelRepo(session)
            if previous_id is not None:
                repo.activate(previous_id)
            else:
                active = repo.get_active()
                if active is not None:
                    active.is_active = False
        try:
            orchestrator.reload_model()          # back to the previous weights
        except Exception:
            log.exception("reload_model also failed for the previous model")
