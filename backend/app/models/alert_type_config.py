from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.postgres import Base
from backend.app.models.enums import Severity, str_enum


class AlertTypeConfig(Base):
    """Staff-managed catalog of alert types: one row per model category."""

    __tablename__ = "alert_type_config"

    alert_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    default_severity: Mapped[Severity] = mapped_column(str_enum(Severity))
    description: Mapped[str] = mapped_column(String(255))
