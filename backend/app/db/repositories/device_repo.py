"""devices (PostgreSQL). Owners may only read devices of their own cars (DT-C2-1)."""
from typing import Any

from sqlalchemy import select

from app.db.models import Car, Device
from app.db.repositories.base import SqlRepo


class DeviceRepo(SqlRepo[Device]):
    model = Device

    def search(self, *, owner_id: int | None = None, car_id: int | None = None, status: str | None = None,
               device_type: str | None = None, q: str | None = None,
               page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """GET /devices. Pass owner_id for owners: only devices on their cars are counted or returned."""
        query = select(Device)
        if owner_id is not None:
            query = query.join(Car, Car.id == Device.car_id).where(Car.owner_id == owner_id)
        if car_id is not None:
            query = query.where(Device.car_id == car_id)
        if status:
            query = query.where(Device.status == status)
        if device_type:
            query = query.where(Device.device_type == device_type)
        if q:
            query = query.where(Device.name.ilike(f"%{q}%"))
        return self.paginate(query.order_by(Device.id), page, page_size)

    def get_for_owner(self, device_id: int, owner_id: int) -> Device | None:
        return self.session.scalar(
            select(Device).join(Car, Car.id == Device.car_id)
            .where(Device.id == device_id, Car.owner_id == owner_id))

    def get_by_car_and_name(self, car_id: int, name: str) -> Device | None:
        return self.session.scalar(select(Device).where(Device.car_id == car_id, Device.name == name))

    def list_for_car(self, car_id: int) -> list[Device]:
        return list(self.session.scalars(select(Device).where(Device.car_id == car_id).order_by(Device.id)))
