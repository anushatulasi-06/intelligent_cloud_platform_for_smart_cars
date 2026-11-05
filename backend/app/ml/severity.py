"""Severity of a new alert comes only from alert_type_config.default_severity (BR-4.3)."""
from sqlalchemy.orm import Session

from app.db.repositories import AlertTypeRepo


class UnknownAlertType(LookupError):
    """No alert_type_config row for a category the model produced (configuration error)."""


class SeverityPolicy:
    def __init__(self, session: Session):
        self.repo = AlertTypeRepo(session)

    def lookup(self, category: str) -> tuple[str, bool]:
        """(default_severity, enabled) for a category."""
        row = self.repo.get(category)
        if row is None or row.default_severity is None:
            raise UnknownAlertType(f"No alert_type_config row for '{category}'")
        return row.default_severity, bool(row.enabled)

    def severity_for(self, category: str) -> str:
        return self.lookup(category)[0]
