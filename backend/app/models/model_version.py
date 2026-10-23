from datetime import date

from sqlalchemy import Date, Float, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.postgres import Base


class ModelVersion(Base):
    """A trained classifier checkpoint; cloud staff activate one at a time (FR-8)."""

    __tablename__ = "model_versions"
    __table_args__ = (
        # Partial unique index: any number of inactive rows, at most one active row.
        Index(
            "uq_model_versions_one_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_label: Mapped[str] = mapped_column(String(50), unique=True)
    trained_date: Mapped[date] = mapped_column(Date)
    accuracy: Mapped[float] = mapped_column(Float)
    macro_f1: Mapped[float] = mapped_column(Float)
    # Checkpoint location: repo path now, S3 key once model artifacts move to S3.
    artifact_path: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=False)
