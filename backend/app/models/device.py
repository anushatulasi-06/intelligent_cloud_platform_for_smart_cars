from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.postgres import Base
from backend.app.models.enums import DevicePosition, DeviceStatus, DeviceType, str_enum


class Device(Base):
    """An IoT device on a car. Only audio devices may upload to POST /device-data."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    device_type: Mapped[DeviceType] = mapped_column(str_enum(DeviceType))
    position: Mapped[DevicePosition | None] = mapped_column(str_enum(DevicePosition))
    serial_number: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[DeviceStatus] = mapped_column(
        str_enum(DeviceStatus), default=DeviceStatus.offline, server_default=DeviceStatus.offline.value
    )
    # {"ssid": ..., "security": ..., "upload_interval_s": ..., "network_type": ...}.
    # Never store a plain-text Wi-Fi password here.
    wifi_config: Mapped[dict] = mapped_column(JSON, default=dict)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # sha256 of the secret the edge client sends with each upload (see core.security).
    device_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    car: Mapped["Car"] = relationship(back_populates="devices")
