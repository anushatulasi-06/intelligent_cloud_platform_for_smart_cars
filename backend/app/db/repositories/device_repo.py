"""devices (PostgreSQL). Owners may only read devices of their own cars (DT-C2-1)."""
from datetime import datetime
import re
from typing import Any

from sqlalchemy import case, func, or_, select, update

from app.db.models import Car, Device
from app.db.repositories.base import SqlRepo


class DeviceRepo(SqlRepo[Device]):
    model = Device

    def get_locked(self, device_id: int) -> Device | None:
        return self.session.scalar(select(Device).where(Device.id == device_id).with_for_update())

    def sweep_offline(self, cutoff: datetime) -> int:
        result = self.session.execute(update(Device).where(
            Device.last_seen < cutoff, Device.status.in_(("online", "configuring"))
        ).values(status="offline").execution_options(synchronize_session="fetch"))
        return result.rowcount

    def search(self, *, owner_id: int | None = None, car_id: int | None = None, status: str | None = None,
               device_type: str | None = None, q: str | None = None,
               page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """GET /devices. Pass owner_id for owners: only devices on their cars are counted or returned."""
        query = select(Device).join(Car, Car.id == Device.car_id)
        if owner_id is not None:
            query = query.where(Car.owner_id == owner_id)
        if car_id is not None:
            query = query.where(Device.car_id == car_id)
        if status:
            query = query.where(Device.status == status)
        if device_type:
            query = query.where(Device.device_type == device_type)
        if q and (text := q.strip()):
            pattern = f"%{text}%"
            # F4's search box accepts a device name or a car identifier/name.
            matches = [Device.name.ilike(pattern), Car.plate.ilike(pattern),
                       (Car.make + " " + Car.model).ilike(pattern)]
            car_label = re.fullmatch(r"(?:(?:car|c)\s*-?\s*)?0*(\d+)", text, re.IGNORECASE)
            if car_label and 0 < (car_number := int(car_label.group(1))) <= 2**31 - 1:
                matches.append(Device.car_id == car_number)
            query = query.where(or_(*matches))
        return self.paginate(query.order_by(Device.id), page, page_size)

    def get_for_owner(self, device_id: int, owner_id: int) -> Device | None:
        return self.session.scalar(
            select(Device).join(Car, Car.id == Device.car_id)
            .where(Device.id == device_id, Car.owner_id == owner_id))

    def get_by_car_and_name(self, car_id: int, name: str) -> Device | None:
        return self.session.scalar(select(Device).where(Device.car_id == car_id, Device.name == name))

    def list_for_car(self, car_id: int) -> list[Device]:
        return list(self.session.scalars(select(Device).where(Device.car_id == car_id).order_by(Device.id)))

    def count_by_status(self) -> dict[str, int]:
        """How many devices are in each state, for the fleet dashboard (F6)."""
        rows = self.session.execute(select(Device.status, func.count()).group_by(Device.status))
        return {status: count for status, count in rows if status}

    def counts_for_cars(self, car_ids: list[int]) -> dict[int, dict[str, int]]:
        """Per car: {"total": n, "online": n}. Cars without devices are not in the result (F2)."""
        online = func.sum(case((Device.status == "online", 1), else_=0))
        rows = self.session.execute(
            select(Device.car_id, func.count(), online)
            .where(Device.car_id.in_(car_ids)).group_by(Device.car_id))
        return {car_id: {"total": total, "online": int(n_online or 0)} for car_id, total, n_online in rows}
