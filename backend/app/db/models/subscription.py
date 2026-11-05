from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, check_in
from app.db.models.constants import ALERT_TYPES, NOTIFY_METHODS


class Subscription(Base):
    """One row per (owner, car, category); a missing row means "do not notify" (BR-1.4)."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("owner_id", "car_id", "alert_type"),
        check_in("alert_type", ALERT_TYPES),
        check_in("notify_method", NOTIFY_METHODS),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"), nullable=False)
    # VARCHAR(16), not the spec's 12: 'environmental' is 13 characters.
    alert_type: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    notify_method: Mapped[str | None] = mapped_column(String(6), server_default=text("'email'"))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
