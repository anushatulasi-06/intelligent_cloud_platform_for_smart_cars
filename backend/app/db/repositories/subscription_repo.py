"""subscriptions (PostgreSQL). One row per (owner, car, alert_type); missing row = do not notify."""
from sqlalchemy import select

from app.db.models import Car, Subscription
from app.db.repositories.base import SqlRepo


class SubscriptionRepo(SqlRepo[Subscription]):
    model = Subscription
    time_column = "updated_at"      # subscriptions have no created_at

    def list_for_owner(self, owner_id: int, car_id: int | None = None) -> list[Subscription]:
        query = select(Subscription).where(Subscription.owner_id == owner_id)
        if car_id is not None:
            query = query.where(Subscription.car_id == car_id)
        return list(self.session.scalars(query.order_by(Subscription.car_id, Subscription.alert_type)))

    def get_for_owner(self, sub_id: int, owner_id: int) -> Subscription | None:
        return self.session.scalar(
            select(Subscription).where(Subscription.id == sub_id, Subscription.owner_id == owner_id))

    def upsert(self, owner_id: int, car_id: int, alert_type: str, enabled: bool,
               notify_method: str | None = None) -> tuple[Subscription, bool]:
        """Create or update one row. Returns (row, created)."""
        row = self.session.scalar(select(Subscription).where(
            Subscription.owner_id == owner_id, Subscription.car_id == car_id,
            Subscription.alert_type == alert_type))
        if row is None:
            row = Subscription(owner_id=owner_id, car_id=car_id, alert_type=alert_type, enabled=enabled,
                               notify_method=notify_method or "email")
            return self.add(row), True
        row.enabled = enabled
        if notify_method is not None:
            row.notify_method = notify_method
        self.session.flush()
        return row, False

    def find(self, owner_id: int, car_id: int, alert_type: str) -> Subscription | None:
        """The row for (owner, car, category), or None (= do not notify, BR-1.4)."""
        return self.session.scalar(select(Subscription).where(
            Subscription.owner_id == owner_id, Subscription.car_id == car_id,
            Subscription.alert_type == alert_type))

    def is_enabled(self, car_id: int, alert_type: str) -> tuple[bool, str | None]:
        """Used before notifying (C4): is the car's owner subscribed to this category?
        Returns (enabled, notify_method); (False, None) when there is no row."""
        row = self.session.scalar(
            select(Subscription).join(Car, Car.id == Subscription.car_id)
            .where(Subscription.car_id == car_id, Subscription.alert_type == alert_type,
                   Subscription.owner_id == Car.owner_id))
        if row is None:
            return False, None
        return bool(row.enabled), row.notify_method
