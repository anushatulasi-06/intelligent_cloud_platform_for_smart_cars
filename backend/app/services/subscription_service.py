"""Alert subscriptions for owners (C1, F3).

One row per (owner, car, category). A missing row means the owner is not notified (BR-1.4),
so turning a category off either updates the row or removes it; both have the same effect.
Alert creation reads these rows through SubscriptionRepo.is_enabled (C4).
"""
from app.db import postgres
from app.db.models import Subscription
from app.db.repositories import CarRepo, SubscriptionRepo
from app.services.errors import Forbidden, NotFound


def public(row: Subscription) -> dict:
    return {"id": row.id, "car_id": row.car_id, "alert_type": row.alert_type,
            "enabled": bool(row.enabled), "notify_method": row.notify_method}


class SubscriptionService:
    def __init__(self, user):
        self.user = user

    def list_for_owner(self, car_id: int | None = None) -> list[dict]:
        with postgres.session_scope() as session:
            if car_id is not None:
                self._check_owner(session, car_id)
            rows = SubscriptionRepo(session).list_for_owner(self.user.id, car_id)
            return [public(r) for r in rows]

    def upsert(self, car_id: int, alert_type: str, enabled: bool,
               notify_method: str) -> tuple[dict, bool]:
        with postgres.session_scope() as session:
            self._check_owner(session, car_id)
            row, created = SubscriptionRepo(session).upsert(
                self.user.id, car_id, alert_type, enabled, notify_method)
            return public(row), created

    def remove(self, sub_id: int) -> None:
        with postgres.session_scope() as session:
            repo = SubscriptionRepo(session)
            row = repo.get_for_owner(sub_id, self.user.id)
            if row is None:            # unknown id and someone else's row answer the same
                raise NotFound("Subscription not found")
            repo.delete(row.id)

    def _check_owner(self, session, car_id: int) -> None:
        if not CarRepo(session).is_owned_by(car_id, self.user.id):
            raise Forbidden("That car does not belong to you")
