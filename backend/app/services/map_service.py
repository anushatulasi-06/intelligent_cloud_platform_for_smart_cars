"""Map markers (C1, F9) and route playback (C1, F10).

A car's marker colour comes from DT-MV-1: how old its last position is, how many alerts are
still open on it, and the highest severity among them. Owners only see their own cars.
"""
from datetime import datetime, timedelta, timezone

from app.db import postgres
from app.db.repositories import AlertRepo, CarRepo, PositionRepo
from app.services.common import as_utc
from app.services.errors import NotFound, Unprocessable

ONLINE_SECONDS = 90          # BR-MV-1: a car is online while its last position is this fresh
URGENT_SEVERITIES = ("high", "critical")
MAX_TRACK_HOURS = 24
MAX_TRACK_POINTS = 500
TRACK_ALERT_LIMIT = 100


def marker_status(age_seconds: float | None, open_alerts: int, top_severity: str | None) -> str:
    """DT-MV-1: offline (grey), online (teal), alert (gold) or urgent (coral)."""
    if age_seconds is None or age_seconds > ONLINE_SECONDS:
        return "offline"
    if open_alerts < 1:
        return "online"
    return "urgent" if top_severity in URGENT_SEVERITIES else "alert"


def car_name(car_id: int, model: str | None) -> str:
    return f"Car {car_id} - {model}" if model else f"Car {car_id}"


class MapService:
    def __init__(self, user, alerts: AlertRepo | None = None, positions: PositionRepo | None = None):
        self.user = user
        self.alerts = alerts or AlertRepo()
        self.positions = positions or PositionRepo()

    def fleet_positions(self, status: str | None = None, sound_type: str | None = None,
                        car_id: int | None = None) -> dict:
        with postgres.session_scope() as session:
            repo = CarRepo(session)
            cars = repo.list_for_owner(self.user.id) if self.user.role == "owner" else repo.list_all()
            cars = [(c.id, c.model) for c in cars]
        if car_id is not None:
            # Narrowing only. An owner asking for someone else's car gets an empty list.
            cars = [c for c in cars if c[0] == car_id]

        car_ids = [c[0] for c in cars]
        last = self.positions.last_per_car(car_ids) if car_ids else {}
        counts = self.alerts.open_counts(car_ids) if car_ids else {}
        of_type = self.alerts.cars_with_open_type(car_ids, sound_type) if sound_type and car_ids else None

        now = datetime.now(timezone.utc)
        out = []
        for cid, model in cars:
            point = last.get(cid)
            open_alerts = counts.get(cid, {}).get("open", 0)
            top = counts.get(cid, {}).get("top")
            age = (now - as_utc(point["ts"])).total_seconds() if point else None
            state = marker_status(age, open_alerts, top)
            if status and status != state:
                continue
            if of_type is not None and cid not in of_type:
                continue
            out.append({
                "car_id": cid,
                "name": car_name(cid, model),
                "lat": point["lat"] if point else None,
                "lon": point["lon"] if point else None,
                "heading": point.get("heading") if point else None,
                "speed_kmh": point.get("speed_kmh") if point else None,
                "position_at": point["ts"] if point else None,
                "status": state,
                "open_alerts": open_alerts,
                "top_severity": top,
            })
        return {"cars": out}


class TrackService:
    def __init__(self, user, alerts: AlertRepo | None = None, positions: PositionRepo | None = None):
        self.user = user
        self.alerts = alerts or AlertRepo()
        self.positions = positions or PositionRepo()

    def get_track(self, car_id: int, from_: datetime | None = None, to: datetime | None = None,
                  max_points: int = MAX_TRACK_POINTS) -> dict:
        self._check_car(car_id)
        to = as_utc(to) or datetime.now(timezone.utc)
        from_ = as_utc(from_) or to - timedelta(hours=MAX_TRACK_HOURS)
        if to < from_:
            raise Unprocessable("from must be earlier than to")
        if to - from_ > timedelta(hours=MAX_TRACK_HOURS):
            raise Unprocessable(f"The range cannot be longer than {MAX_TRACK_HOURS} hours")

        points = self.positions.track(car_id, from_, to, max_points)
        found = self.alerts.search(car_ids=[car_id], from_=from_, to=to, page=1,
                                   page_size=TRACK_ALERT_LIMIT)
        return {"car_id": car_id, "points": points,
                "alerts": [self._place(a, points) for a in found["items"]]}

    def _check_car(self, car_id: int) -> None:
        with postgres.session_scope() as session:
            car = CarRepo(session).get(car_id)
        # Another owner's car answers 404, exactly like an unknown id.
        if car is None or (self.user.role == "owner" and car.owner_id != self.user.id):
            raise NotFound("Car not found")

    @staticmethod
    def _place(alert: dict, points: list[dict]) -> dict:
        """Alerts carry their own position; when one is missing, use the nearest point in time."""
        lat, lon = alert.get("lat"), alert.get("lon")
        stamp = as_utc(alert.get("timestamp"))
        if (lat is None or lon is None) and points and stamp is not None:
            near = min(points, key=lambda p: abs((as_utc(p["ts"]) - stamp).total_seconds()))
            lat, lon = near["lat"], near["lon"]
        return {"id": alert["id"], "timestamp": alert.get("timestamp"), "lat": lat, "lon": lon,
                "sound_type": alert["sound_type"], "severity": alert.get("severity"),
                "status": alert["status"]}
