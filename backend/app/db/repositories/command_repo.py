"""Persistent command lifecycle. Device rows are locked by the service during edge requests."""
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.db.models.device_command import DeviceCommand
from app.db.repositories.base import SqlRepo


class CommandRepo(SqlRepo[DeviceCommand]):
    model = DeviceCommand

    def expire_stale(self, now: datetime) -> int:
        result = self.session.execute(update(DeviceCommand).where(
            DeviceCommand.status.in_(("queued", "delivered")), DeviceCommand.expires_at <= now
        ).values(status="expired").execution_options(synchronize_session="fetch"))
        return result.rowcount

    def queued(self, device_id: int, command: str) -> DeviceCommand | None:
        return self.session.scalar(select(DeviceCommand).where(
            DeviceCommand.device_id == device_id, DeviceCommand.command == command,
            DeviceCommand.status == "queued"))

    def has_open(self, device_id: int, command: str, now: datetime) -> bool:
        return self.session.scalar(select(DeviceCommand.id).where(
            DeviceCommand.device_id == device_id, DeviceCommand.command == command,
            DeviceCommand.status.in_(("queued", "delivered")), DeviceCommand.expires_at > now).limit(1)) is not None

    def supersede_config(self, device_id: int) -> None:
        """A new Wi-Fi configuration replaces any earlier queued or delivered version."""
        self.session.execute(update(DeviceCommand).where(
            DeviceCommand.device_id == device_id, DeviceCommand.command == "sync_config",
            DeviceCommand.status.in_(("queued", "delivered"))).values(status="expired"))

    def enqueue(self, device_id: int, command: str, payload: dict, now: datetime) -> DeviceCommand:
        return self.add(DeviceCommand(device_id=device_id, command=command, payload=payload,
                                      status="queued", created_at=now, expires_at=now + timedelta(minutes=10)))

    def pending_for(self, device_id: int, now: datetime) -> list[DeviceCommand]:
        self.expire_stale(now)
        rows = list(self.session.scalars(select(DeviceCommand).where(
            DeviceCommand.device_id == device_id, DeviceCommand.status == "queued"
        ).order_by(DeviceCommand.id).with_for_update(skip_locked=True)))
        for row in rows:
            row.status, row.delivered_at = "delivered", now
        self.session.flush()
        return rows

    def acknowledge(self, device_id: int, command_id: int, ok: bool, detail: str | None,
                    now: datetime) -> DeviceCommand | None:
        return self.session.scalar(update(DeviceCommand).where(
            DeviceCommand.id == command_id, DeviceCommand.device_id == device_id,
            DeviceCommand.status == "delivered", DeviceCommand.expires_at > now
        ).values(status="acknowledged" if ok else "failed", acked_at=now, detail=detail)
          .returning(DeviceCommand).execution_options(populate_existing=True, synchronize_session="fetch"))

    def history(self, device_id: int, status: str | None = None, limit: int = 20) -> list[DeviceCommand]:
        query = select(DeviceCommand).where(DeviceCommand.device_id == device_id)
        if status:
            query = query.where(DeviceCommand.status == status)
        return list(self.session.scalars(query.order_by(DeviceCommand.created_at.desc(), DeviceCommand.id.desc()).limit(limit)))

    def for_car(self, car_id: int, limit: int = 20) -> list[DeviceCommand]:
        from app.db.models import Device
        return list(self.session.scalars(select(DeviceCommand).join(Device).where(Device.car_id == car_id)
                    .order_by(DeviceCommand.created_at.desc(), DeviceCommand.id.desc()).limit(limit)))
