from sqlalchemy import Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, check_in
from app.db.models.constants import SEVERITIES


class AlertTypeConfig(Base):
    """Default severity per sound category. Changes affect new alerts only (BR-CF-3)."""

    __tablename__ = "alert_type_config"
    __table_args__ = (check_in("default_severity", SEVERITIES),)

    # VARCHAR(16), not the spec's 12: 'environmental' is 13 characters.
    alert_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    default_severity: Mapped[str | None] = mapped_column(String(8))
    description: Mapped[str | None] = mapped_column(String(120))
    enabled: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
