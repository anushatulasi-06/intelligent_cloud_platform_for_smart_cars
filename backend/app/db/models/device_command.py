from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, check_in
from app.db.models.constants import DEVICE_COMMANDS, COMMAND_STATUSES


class DeviceCommand(Base):
    __tablename__ = "device_commands"
    __table_args__ = (
        check_in("command", DEVICE_COMMANDS),
        check_in("status", COMMAND_STATUSES),
        Index("ix_device_commands_device_id_created_at", "device_id", "created_at"),
        Index("uq_device_commands_queued", "device_id", "command", unique=True,
              postgresql_where=text("status = 'queued'"), sqlite_where=text("status = 'queued'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    command: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'queued'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(200))
