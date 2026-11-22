"""alerts (MongoDB). Owner scope: pass the owner's car ids (CarRepo.ids_for_owner) as `car_ids`;
the filter is part of the Mongo query, so other owners' alerts are never read (BR-4.5)."""
from datetime import datetime
from typing import Any

from pymongo import DESCENDING, ReturnDocument

from app.db import mongo
from app.db.repositories.base import MongoRepo

NEWEST_FIRST = [("timestamp", DESCENDING)]
BY_DAY = {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}

# What GET /admin/reports/alerts may group by (C1).
GROUP_KEYS = {"sound_type": "$sound_type", "severity": "$severity", "status": "$status",
              "car": "$car_id", "day": BY_DAY}


def build_filter(*, car_ids: list[int] | None = None, car_id: int | None = None, severity: str | None = None,
                 status: str | None = None, sound_type: str | None = None, is_anomaly: bool | None = None,
                 from_: datetime | None = None, to: datetime | None = None) -> dict[str, Any]:
    """Mongo filter for GET /alerts and /alerts/summary. car_ids=None means all cars (cloud staff)."""
    query: dict[str, Any] = {}
    if car_ids is not None:
        allowed = [c for c in car_ids if car_id is None or c == car_id]
        query["car_id"] = {"$in": allowed}
    elif car_id is not None:
        query["car_id"] = car_id
    for key, value in (("severity", severity), ("status", status), ("sound_type", sound_type)):
        if value:
            query[key] = value
    if is_anomaly is not None:
        query["is_anomaly"] = is_anomaly
    if from_ or to:
        query["timestamp"] = {**({"$gte": from_} if from_ else {}), **({"$lte": to} if to else {})}
    return query


class AlertRepo(MongoRepo):
    collection_name = mongo.ALERTS
    time_field = "timestamp"

    def search(self, *, page: int = 1, page_size: int = 20, **filters) -> dict[str, Any]:
        return self.paginate(build_filter(**filters), page, page_size, sort=NEWEST_FIRST)

    def get_scoped(self, alert_id: str, car_ids: list[int] | None) -> dict | None:
        """One alert; None when it does not exist or (for owners) is not on one of their cars."""
        query: dict[str, Any] = {"_id": self._oid(alert_id)}
        if car_ids is not None:
            query["car_id"] = {"$in": list(car_ids)}
        return self.find_one(query)

    def get_by_record_id(self, record_id: str) -> dict | None:
        return self.find_one({"record_id": record_id})

    def upsert_by_record_id(self, record_id: str, doc: dict[str, Any]) -> dict:
        """Exactly one alert per record (BR-4.4): inserts on first call, returns the existing alert after."""
        found = self.collection.find_one_and_update(
            {"record_id": record_id}, {"$setOnInsert": {**doc, "record_id": record_id}},
            upsert=True, return_document=ReturnDocument.AFTER)
        return self.to_public(found)

    def update_status(self, alert_id: str, fields: dict[str, Any]) -> dict | None:
        return self.update(alert_id, fields)

    def transition(self, alert_id: str, car_ids: list[int] | None, from_status: str,
                   fields: dict[str, Any]) -> dict | None:
        """Atomically change an alert only if it still has `from_status` (and, for owners, is on one of
        their cars). Returns the updated alert, or None if it changed in the meantime."""
        query: dict[str, Any] = {"_id": self._oid(alert_id), "status": from_status}
        if car_ids is not None:
            query["car_id"] = {"$in": list(car_ids)}
        doc = self.collection.find_one_and_update(query, {"$set": fields}, return_document=ReturnDocument.AFTER)
        return self.to_public(doc)

    def summary(self, **filters) -> dict[str, Any]:
        """GET /alerts/summary in ONE aggregation: { total, by_status, by_severity, by_type, anomalies }."""
        def group(field: str) -> list[dict]:
            return [{"$group": {"_id": f"${field}", "n": {"$sum": 1}}}]

        pipeline = [
            {"$match": build_filter(**filters)},
            {"$facet": {
                "total": [{"$count": "n"}],
                "by_status": group("status"),
                "by_severity": group("severity"),
                "by_type": group("sound_type"),
                "anomalies": [{"$match": {"is_anomaly": True}}, {"$count": "n"}],
            }},
        ]
        facet = next(iter(self.collection.aggregate(pipeline)), {})
        count = lambda key: (facet.get(key) or [{"n": 0}])[0]["n"]  # noqa: E731
        by = lambda key: {r["_id"]: r["n"] for r in facet.get(key, []) if r["_id"] is not None}  # noqa: E731
        return {"total": count("total"), "by_status": by("by_status"), "by_severity": by("by_severity"),
                "by_type": by("by_type"), "anomalies": count("anomalies")}

    def per_day(self, **filters) -> list[dict[str, Any]]:
        """Alerts per calendar day (UTC), oldest first. Days with no alerts are left out."""
        rows = self.collection.aggregate([
            {"$match": build_filter(**filters)},
            {"$group": {"_id": BY_DAY, "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ])
        return [{"date": r["_id"], "count": r["count"]} for r in rows]

    def report(self, group_by: str, **filters) -> list[dict[str, Any]]:
        """GET /admin/reports/alerts: count, anomalies and average confidence per group."""
        rows = self.collection.aggregate([
            {"$match": build_filter(**filters)},
            {"$group": {"_id": GROUP_KEYS[group_by], "count": {"$sum": 1},
                        "anomalies": {"$sum": {"$cond": [{"$eq": ["$is_anomaly", True]}, 1, 0]}},
                        "avg_confidence": {"$avg": "$confidence"}}},
            {"$sort": {"count": -1, "_id": 1}},
        ])
        out = []
        for r in rows:
            avg = r["avg_confidence"]
            out.append({"key": str(r["_id"]), "count": r["count"], "anomalies": r["anomalies"],
                        "avg_confidence": round(avg, 3) if avg is not None else None})
        return out

    def cars_with_open_type(self, car_ids: list[int], sound_type: str) -> set[int]:
        """Cars that have at least one unresolved alert of this category (F9 sound-type filter)."""
        docs = self.collection.find(
            {"car_id": {"$in": list(car_ids)}, "status": {"$ne": "resolved"}, "sound_type": sound_type},
            {"car_id": 1})
        return {d["car_id"] for d in docs}

    def open_counts(self, car_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Per car: number of unresolved alerts and their highest severity (map markers, DT-MV-1)."""
        rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        out: dict[int, dict[str, Any]] = {}
        for doc in self.collection.find({"car_id": {"$in": list(car_ids)}, "status": {"$ne": "resolved"}},
                                        {"car_id": 1, "severity": 1}):
            entry = out.setdefault(doc["car_id"], {"open": 0, "top": None})
            entry["open"] += 1
            if entry["top"] is None or rank.get(doc.get("severity"), 0) > rank.get(entry["top"], 0):
                entry["top"] = doc.get("severity")
        return out
