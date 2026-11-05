"""cars (PostgreSQL). Owner-scoped methods filter by owner_id in the query (BR-1.3)."""
from datetime import datetime

from sqlalchemy import select

from app.db.models import Car
from app.db.repositories.base import SqlRepo


class CarRepo(SqlRepo[Car]):
    model = Car

    def list_all(self) -> list[Car]:
        return list(self.session.scalars(select(Car).order_by(Car.id)))

    def list_for_owner(self, owner_id: int) -> list[Car]:
        return list(self.session.scalars(select(Car).where(Car.owner_id == owner_id).order_by(Car.id)))

    def cars_of_owner(self, owner_id: int) -> list[int]:
        """Car ids an owner may see; pass these into Mongo queries (alerts, positions, records)."""
        return list(self.session.scalars(select(Car.id).where(Car.owner_id == owner_id).order_by(Car.id)))

    ids_for_owner = cars_of_owner     # older name, kept for existing callers

    def is_owned_by(self, car_id: int, owner_id: int) -> bool:
        return self.session.scalar(
            select(Car.id).where(Car.id == car_id, Car.owner_id == owner_id)) is not None

    def get_by_plate(self, plate: str) -> Car | None:
        return self.session.scalar(select(Car).where(Car.plate == plate))

    def update_position(self, car_id: int, lat: float, lon: float, at: datetime) -> Car | None:
        return self.update(car_id, {"last_lat": lat, "last_lon": lon, "last_position_at": at})
