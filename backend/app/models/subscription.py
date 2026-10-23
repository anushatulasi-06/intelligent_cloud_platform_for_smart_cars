from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.postgres import Base
from backend.app.models.enums import NotifyMethod, str_enum


class Subscription(Base):
    """Whether an owner is notified about one alert type for one of their cars."""

    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("car_id", "alert_type", name="uq_subscription_car_alert_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"))
    alert_type: Mapped[str] = mapped_column(
        String(32), ForeignKey("alert_type_config.alert_type", ondelete="CASCADE")
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    notify_method: Mapped[NotifyMethod] = mapped_column(
        str_enum(NotifyMethod), default=NotifyMethod.in_app
    )

    owner: Mapped["User"] = relationship(back_populates="subscriptions")
    car: Mapped["Car"] = relationship(back_populates="subscriptions")
