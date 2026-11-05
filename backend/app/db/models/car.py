from datetime import datetime

from sqlalchemy import DateTime, Double, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class Car(Base):
    __tablename__ = "cars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    make: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    plate: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True)
    last_lat: Mapped[float | None] = mapped_column(Double)
    last_lon: Mapped[float | None] = mapped_column(Double)
    last_position_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"Car(id={self.id}, owner_id={self.owner_id}, plate={self.plate!r})"
