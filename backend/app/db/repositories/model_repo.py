"""model_versions (PostgreSQL). Exactly one active model at a time (BR-CF-1)."""
from decimal import Decimal

from sqlalchemy import select, update

from app.db.models import ModelVersion
from app.db.models.constants import THRESHOLD_MAX, THRESHOLD_MIN
from app.db.repositories.base import SqlRepo


class ModelRepo(SqlRepo[ModelVersion]):
    model = ModelVersion
    time_column = None

    def list_all(self) -> list[ModelVersion]:
        return list(self.session.scalars(select(ModelVersion).order_by(ModelVersion.id.desc())))

    def get_active(self) -> ModelVersion | None:
        return self.session.scalar(select(ModelVersion).where(ModelVersion.is_active.is_(True)))

    def get_by_label(self, label: str) -> ModelVersion | None:
        return self.session.scalar(select(ModelVersion).where(ModelVersion.version_label == label))

    def activate(self, model_id: int) -> ModelVersion | None:
        """Deactivate every other version, then activate this one, in the caller's transaction.
        Run inside session_scope() so a failure leaves the old model active."""
        target = self.get(model_id)
        if target is None:
            return None
        # Deactivate first and flush, so the partial unique index never sees two active rows.
        self.session.execute(update(ModelVersion).where(ModelVersion.id != model_id,
                                                        ModelVersion.is_active.is_(True))
                             .values(is_active=False))
        self.session.flush()
        target.is_active = True
        self.session.flush()
        self.session.refresh(target)
        return target

    def set_threshold(self, model_id: int, threshold: float) -> ModelVersion | None:
        """BR-CF-2: 0.50 to 0.95 (the database CHECK enforces it too)."""
        if not THRESHOLD_MIN <= threshold <= THRESHOLD_MAX:
            raise ValueError(f"confidence_threshold must be between {THRESHOLD_MIN} and {THRESHOLD_MAX}")
        return self.update(model_id, {"confidence_threshold": Decimal(str(round(threshold, 2)))})
