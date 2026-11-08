"""device_data_records (MongoDB): the audio processing queue (C3).

States: pending -> processing (120 s lease) -> processed | back to pending | failed after 3 attempts.
"""
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument

from app.db import mongo
from app.db.repositories.base import MongoRepo

LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
STATUSES = ("pending", "processing", "processed", "failed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecordRepo(MongoRepo):
    collection_name = mongo.DEVICE_DATA_RECORDS
    time_field = "received_timestamp"

    def get_by_record_id(self, record_id: str) -> dict | None:
        return self.find_one({"record_id": record_id})

    def get_by_clip_id(self, clip_id: str) -> dict | None:
        """Duplicate upload protection (BR-3.3)."""
        return self.find_one({"clip_id": clip_id})

    def create_pending(self, record: dict[str, Any]) -> dict:
        doc = {"status": "pending", "processed": False, "attempts": 0, "lease_until": None, "error": None,
               "alert_id": None, "received_timestamp": _now(), **record}
        if doc.get("clip_id") is None:
            doc.pop("clip_id", None)            # sparse unique index: leave the field out, not null
        return self.add(doc)

    def claim_next(self, lease_seconds: int = LEASE_SECONDS) -> dict | None:
        """Atomically take the oldest pending record, or one whose lease expired (BR-3.4)."""
        now = _now()
        # A crash on the last attempt must not leave an endlessly reclaimable lease.
        self.collection.update_many(
            {"attempts": {"$gte": MAX_ATTEMPTS}, "$or": [
                {"status": "pending"}, {"status": "processing", "lease_until": {"$lte": now}}]},
            {"$set": {"status": "failed", "processed": False, "lease_until": None,
                      "error": "Processing lease expired after the final attempt"}})
        doc = self.collection.find_one_and_update(
            {"attempts": {"$lt": MAX_ATTEMPTS}, "$or": [
                {"status": "pending"}, {"status": "processing", "lease_until": {"$lte": now}}]},
            {"$set": {"status": "processing", "lease_until": now + timedelta(seconds=lease_seconds),
                      "lease_token": str(uuid4())},
             "$inc": {"attempts": 1}},
            sort=[("received_timestamp", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return self.to_public(doc)

    def mark_processed(self, record_id: str, alert_id: str | None, *, attempt: int | None = None,
                       lease_token: str | None = None) -> dict | None:
        return self._set(record_id, {"status": "processed", "processed": True, "alert_id": alert_id,
                                     "processed_at": _now(), "lease_until": None, "lease_token": None,
                                     "error": None}, attempt=attempt, lease_token=lease_token)

    def mark_error(self, record_id: str, error: str, max_attempts: int = MAX_ATTEMPTS,
                   *, attempt: int | None = None, lease_token: str | None = None) -> dict | None:
        """Back to pending for another try, or failed once attempts are used up (BR-3.5)."""
        rec = self.get_by_record_id(record_id)
        if rec is None:
            return None
        status = "failed" if rec.get("attempts", 0) >= max_attempts else "pending"
        return self._set(record_id, {"status": status, "processed": False,
                                    "error": error[:500], "lease_until": None, "lease_token": None},
                         attempt=attempt, lease_token=lease_token)

    def retry_failed(self, record_id: str) -> dict | None:
        """POST /device-data/{id}/retry: only failed records go back to the queue (else None -> 409)."""
        doc = self.collection.find_one_and_update(
            {"record_id": record_id, "status": "failed"},
            {"$set": {"status": "pending", "processed": False, "attempts": 0,
                      "error": None, "lease_until": None, "lease_token": None}},
            return_document=ReturnDocument.AFTER)
        return self.to_public(doc)

    def search(self, *, car_ids: list[int] | None = None, status: str | None = None, car_id: int | None = None,
               device_id: int | None = None, from_: datetime | None = None, to: datetime | None = None,
               page: int = 1, page_size: int = 20) -> dict[str, Any]:
        query: dict[str, Any] = {}
        if car_ids is not None:
            query["car_id"] = {"$in": [c for c in car_ids if car_id is None or c == car_id]}
        elif car_id is not None:
            query["car_id"] = car_id
        if status:
            query["status"] = status
        if device_id is not None:
            query["device_id"] = device_id
        if from_ or to:
            query["received_timestamp"] = {**({"$gte": from_} if from_ else {}), **({"$lte": to} if to else {})}
        return self.paginate(query, page, page_size, sort=[("received_timestamp", DESCENDING)])

    def stats(self, window_hours: int = 12, now: datetime | None = None) -> dict[str, Any]:
        """GET /device-data/stats: counts per status and clips per hour for the window."""
        now = now or _now()
        counts = {s: 0 for s in STATUSES}
        for row in self.collection.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
            if row["_id"] in counts:
                counts[row["_id"]] = row["n"]
        # The GUI labels this card "Processed (24 h)"; other cards show live backlog.
        cutoff = now - timedelta(hours=24)
        counts["processed"] = self.collection.count_documents({"status": "processed", "$or": [
            {"processed_at": {"$gte": cutoff}},
            {"processed_at": None, "received_timestamp": {"$gte": cutoff}}]})
        start = (now - timedelta(hours=window_hours)).replace(minute=0, second=0, microsecond=0)
        hours = {start + timedelta(hours=i): 0 for i in range(window_hours + 1)}
        for doc in self.collection.find({"received_timestamp": {"$gte": start}}, {"received_timestamp": 1}):
            ts = doc["received_timestamp"]
            ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            bucket = ts.replace(minute=0, second=0, microsecond=0)
            if bucket in hours:
                hours[bucket] += 1
        per_hour = [{"hour": h.isoformat(), "count": n} for h, n in sorted(hours.items())][-window_hours:]
        totals = next(iter(self.collection.aggregate([{"$group": {
            "_id": None, "bytes_stored": {"$sum": "$size_bytes"}}}])), {})
        latency, completed = 0.0, 0
        for doc in self.collection.find({"status": "processed", "processed_at": {"$gte": start}},
                                         {"processed_at": 1, "received_timestamp": 1}):
            received, processed = doc.get("received_timestamp"), doc.get("processed_at")
            if received and processed:
                latency += max(0, (processed - received).total_seconds())
                completed += 1
        return {"counts": counts, "per_hour": per_hour,
                "bytes_stored": totals.get("bytes_stored", 0),
                "avg_latency_s": latency / completed if completed else None}

    def _set(self, record_id: str, fields: dict[str, Any], *, attempt: int | None = None,
             lease_token: str | None = None) -> dict | None:
        query = {"record_id": record_id}
        if attempt is not None:
            query.update(status="processing", attempts=attempt, lease_until={"$gt": _now()})
        if lease_token is not None:
            query["lease_token"] = lease_token
        doc = self.collection.find_one_and_update(query, {"$set": fields},
                                                  return_document=ReturnDocument.AFTER)
        return self.to_public(doc)
