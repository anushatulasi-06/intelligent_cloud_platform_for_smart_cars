"""car_positions (MongoDB): { car_id, ts, lat, lon, speed_kmh, heading }. Owner scope via car_ids."""
from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING

from app.db import mongo
from app.db.repositories.base import MongoRepo


class PositionRepo(MongoRepo):
    collection_name = mongo.CAR_POSITIONS
    time_field = "ts"

    def add_position(self, car_id: int, ts: datetime, lat: float, lon: float,
                     speed_kmh: float | None = None, heading: float | None = None) -> dict:
        return self.add({"car_id": car_id, "ts": ts, "lat": lat, "lon": lon,
                         "speed_kmh": speed_kmh, "heading": heading})

    def last_per_car(self, car_ids: list[int]) -> dict[int, dict]:
        """Newest position of each car (uses the {car_id:1, ts:-1} index)."""
        rows = self.collection.aggregate([
            {"$match": {"car_id": {"$in": list(car_ids)}}},
            {"$sort": {"car_id": 1, "ts": -1}},
            {"$group": {"_id": "$car_id", "doc": {"$first": "$$ROOT"}}},
        ])
        return {r["_id"]: self.to_public(r["doc"]) for r in rows}

    def track(self, car_id: int, from_: datetime, to: datetime, max_points: int = 2000) -> list[dict[str, Any]]:
        """Points of one car in a time range, oldest first, thinned evenly to at most max_points."""
        docs = list(self.collection.find({"car_id": car_id, "ts": {"$gte": from_, "$lte": to}})
                    .sort([("ts", ASCENDING)]))
        if max_points > 0 and len(docs) > max_points:
            step = len(docs) / max_points
            docs = [docs[int(i * step)] for i in range(max_points - 1)] + [docs[-1]]
        return [self.to_public(d) for d in docs]

    def latest(self, car_id: int) -> dict | None:
        doc = next(iter(self.collection.find({"car_id": car_id}).sort([("ts", DESCENDING)]).limit(1)), None)
        return self.to_public(doc)
