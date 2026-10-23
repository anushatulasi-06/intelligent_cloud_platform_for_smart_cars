"""MongoDB client and collections for high-volume, append-heavy records.

alerts (one document per AI-generated alert):
    car_id, device_id, record_id, sound_type, confidence, is_anomaly, severity,
    status ("new" -> "acknowledged" -> "resolved"), model_version, notified,
    timestamp, acknowledged_by, acknowledged_at, resolved_by, resolved_at

device_data_records (one document per uploaded audio clip):
    device_id, car_id, s3_object_key, received_timestamp, processed, error
"""
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from backend.app.core.config import settings

ALERTS = "alerts"
DEVICE_DATA_RECORDS = "device_data_records"

ALERT_STATUSES = ("new", "acknowledged", "resolved")

_client: MongoClient | None = None


def get_mongo() -> Database:
    """Shared database handle (MongoClient is thread-safe and pools connections)."""
    global _client
    if _client is None:
        _client = MongoClient(settings.mongo_url)
    return _client[settings.mongo_db]


def ensure_indexes(db: Database) -> None:
    alerts = db[ALERTS]
    # Owner alert history (F3): filter by car, newest first.
    alerts.create_index([("car_id", ASCENDING), ("timestamp", DESCENDING)])
    # Cloud staff fleet monitor (F6): open alerts, newest first.
    alerts.create_index([("status", ASCENDING), ("timestamp", DESCENDING)])

    records = db[DEVICE_DATA_RECORDS]
    # Pipeline polls for unprocessed clips in arrival order.
    records.create_index([("processed", ASCENDING), ("received_timestamp", ASCENDING)])
    records.create_index([("s3_object_key", ASCENDING)], unique=True)
