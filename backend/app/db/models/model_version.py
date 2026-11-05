from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class ModelVersion(Base):
    """A trained CNN version. At most one row has is_active = true (partial unique index)."""

    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint("confidence_threshold BETWEEN 0.50 AND 0.95", name="confidence_threshold"),
        # Only rows with is_active = true are indexed, so a second active row violates uniqueness.
        Index("uq_model_versions_one_active", "is_active", unique=True,
              postgresql_where=text("is_active"), sqlite_where=text("is_active")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str | None] = mapped_column(String(20), server_default=text("'audio_classifier'"))
    version_label: Mapped[str | None] = mapped_column(String(30), unique=True)
    trained_date: Mapped[date | None] = mapped_column(Date)
    accuracy: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    confidence_threshold: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), server_default=text("0.75"))
    artifact_path: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))

    def __repr__(self) -> str:
        return f"ModelVersion(id={self.id}, label={self.version_label!r}, active={self.is_active})"
