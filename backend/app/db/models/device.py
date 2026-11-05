from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, check_in
from app.db.models.constants import DEVICE_STATUSES, DEVICE_TYPES, IP_MODES, WIFI_SECURITY


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("car_id", "name"),
        check_in("device_type", DEVICE_TYPES),
        check_in("status", DEVICE_STATUSES),
        check_in("wifi_security", WIFI_SECURITY),
        check_in("ip_mode", IP_MODES),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    device_type: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str | None] = mapped_column(String(12), server_default=text("'registered'"))
    wifi_ssid: Mapped[str | None] = mapped_column(String(32))
    wifi_security: Mapped[str | None] = mapped_column(String(5))
    wifi_password_enc: Mapped[bytes | None] = mapped_column(LargeBinary)   # BYTEA, Fernet token; never returned
    ip_mode: Mapped[str | None] = mapped_column(String(6))
    device_key_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)  # SHA-256 hex of the device key
    firmware: Mapped[str | None] = mapped_column(String(20))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"Device(id={self.id}, car_id={self.car_id}, name={self.name!r}, status={self.status!r})"
