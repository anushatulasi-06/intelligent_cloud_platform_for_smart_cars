from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.postgres import Base


class Car(Base):
    __tablename__ = "cars"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    make: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped["User"] = relationship(back_populates="cars")
    devices: Mapped[list["Device"]] = relationship(back_populates="car", cascade="all, delete-orphan")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="car", cascade="all, delete-orphan"
    )
