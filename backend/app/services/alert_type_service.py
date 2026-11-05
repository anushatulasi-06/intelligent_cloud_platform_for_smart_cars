"""Alert types (F8a). Changing a type affects NEW alerts only (BR-CF-3): this service never reads or
rewrites existing alert documents; the orchestrator reads the current default when it creates an alert."""
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AlertTypeConfig
from app.db.models.constants import SEVERITIES
from app.db.repositories import AlertTypeRepo
from app.services.errors import NotFound, Unprocessable

UPDATABLE = {"default_severity", "description", "enabled"}


class AlertTypeService:
    def __init__(self, session: Session):
        self.repo = AlertTypeRepo(session)

    def list_types(self) -> list[AlertTypeConfig]:
        return self.repo.list_all()

    def update_type(self, alert_type: str, changes: dict[str, Any]) -> AlertTypeConfig:
        if self.repo.get(alert_type) is None:
            raise NotFound(f"Unknown alert type '{alert_type}'")
        unknown = set(changes) - UPDATABLE
        if unknown:
            raise Unprocessable(f"Cannot change {', '.join(sorted(unknown))}")
        if "default_severity" in changes and changes["default_severity"] not in SEVERITIES:
            raise Unprocessable("default_severity must be one of low, medium, high, critical")
        return self.repo.update(alert_type, changes)
