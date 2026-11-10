"""Dashboard data for the owner view (F2) and the fleet view (F6), plus the alert report.

Owners only ever see their own cars: the car ids go into the Mongo query, never into a filter
applied after reading (BR-1.3).
"""
from datetime import datetime, timedelta

from app.db import postgres
from app.db.repositories import AlertRepo, CarRepo, DeviceRepo
from app.db.repositories.alert_repo import GROUP_KEYS
from app.services.common import as_utc, start_of_today
from app.services.errors import Unprocessable

RECENT_ALERTS = 5
MAX_WINDOW_DAYS = 90
OPEN_STATUSES = ("new", "acknowledged")


class DashboardService:
    def __init__(self, alerts: AlertRepo | None = None):
        self.alerts = alerts or AlertRepo()

    def owner_overview(self, owner_id: int) -> dict:
        with postgres.session_scope() as session:
            cars = CarRepo(session).list_for_owner(owner_id)
            car_ids = [c.id for c in cars]
            devices = DeviceRepo(session).counts_for_cars(car_ids)
            rows = []
            for car in cars:
                counts = devices.get(car.id, {"total": 0, "online": 0})
                rows.append({"id": car.id, "make": car.make, "model": car.model,
                             "devices_total": counts["total"], "devices_online": counts["online"],
                             "status": "online" if counts["online"] else "offline"})

        today = self.alerts.summary(car_ids=car_ids, from_=start_of_today())
        everything = self.alerts.summary(car_ids=car_ids)
        unresolved = sum(everything["by_status"].get(s, 0) for s in OPEN_STATUSES)
        recent = self.alerts.search(car_ids=car_ids, page=1, page_size=RECENT_ALERTS)
        return {
            "cars": rows,
            "counts": {"alerts_today": today["total"], "unresolved": unresolved},
            "recent_alerts": recent["items"],
        }

    def fleet_summary(self, window_days: int) -> dict:
        if not 1 <= window_days <= MAX_WINDOW_DAYS:
            raise Unprocessable(f"window_days must be between 1 and {MAX_WINDOW_DAYS}")
        with postgres.session_scope() as session:
            total_cars = CarRepo(session).count_all()
            devices = DeviceRepo(session).count_by_status()

        today = start_of_today()
        window_start = today - timedelta(days=window_days - 1)
        today_counts = self.alerts.summary(from_=today)
        window_counts = self.alerts.summary(from_=window_start)
        return {
            "total_cars": total_cars,
            "devices": devices,
            "alerts_today": today_counts["total"],
            "anomalies_today": today_counts["anomalies"],
            "alerts_by_type": window_counts["by_type"],
            "alerts_per_day": self._per_day(window_start, window_days),
        }

    def alert_report(self, from_: datetime, to: datetime, group_by: str,
                     car_id: int | None = None) -> dict:
        if group_by not in GROUP_KEYS:
            raise Unprocessable(f"group_by must be one of {', '.join(sorted(GROUP_KEYS))}")
        from_, to = as_utc(from_), as_utc(to)
        if to < from_:
            raise Unprocessable("from must be earlier than to")
        if to - from_ > timedelta(days=MAX_WINDOW_DAYS):
            raise Unprocessable(f"The range cannot be longer than {MAX_WINDOW_DAYS} days")
        rows = self.alerts.report(group_by, from_=from_, to=to, car_id=car_id)
        return {"group_by": group_by, "rows": rows}

    def _per_day(self, window_start: datetime, days: int) -> list[dict]:
        """One entry per day of the window, so the chart has no gaps."""
        counted = {r["date"]: r["count"] for r in self.alerts.per_day(from_=window_start)}
        dates = [(window_start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
        return [{"date": d, "count": counted.get(d, 0)} for d in dates]
