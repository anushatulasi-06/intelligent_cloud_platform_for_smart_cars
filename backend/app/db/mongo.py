"""MongoDB: client, collections, indexes and health ping.

Only repositories (app/db/repositories/) should use collections to query.

Usage:
    from app.db.mongo import get_collection
    alerts = get_collection("alerts")
"""
import time

from pymongo import ASCENDING, DESCENDING, IndexModel, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import get_settings

_client: MongoClient | None = None

# Collection names (C5.3.6 store selection).
ALERTS = "alerts"
DEVICE_DATA_RECORDS = "device_data_records"
CAR_POSITIONS = "car_positions"

# Indexes from the Component Design doc (C3.3.6, C4.3.6, C5.3.6).
INDEXES: dict[str, list[IndexModel]] = {
    ALERTS: [
        IndexModel([("record_id", ASCENDING)], unique=True, name="record_id_unique"),          # idempotent create
        IndexModel([("car_id", ASCENDING), ("timestamp", DESCENDING)], name="car_time"),        # owner lists
        IndexModel([("status", ASCENDING), ("severity", ASCENDING), ("timestamp", DESCENDING)],
                   name="status_severity_time"),                                                 # fleet monitor
        IndexModel([("is_anomaly", ASCENDING), ("timestamp", DESCENDING)], name="anomaly_time"), # review queue
    ],
    DEVICE_DATA_RECORDS: [
        IndexModel([("record_id", ASCENDING)], unique=True, name="record_id_unique"),
        IndexModel([("clip_id", ASCENDING)], unique=True, sparse=True, name="clip_id_unique"),
        IndexModel([("status", ASCENDING), ("received_timestamp", ASCENDING)], name="queue_scan"),
        IndexModel([("car_id", ASCENDING), ("received_timestamp", DESCENDING)], name="car_time"),
    ],
    CAR_POSITIONS: [
        IndexModel([("car_id", ASCENDING), ("ts", DESCENDING)], name="car_ts"),        # latest per car, tracks
    ],
}


def get_client() -> MongoClient:
    """The app client, created on first use. pymongo connects lazily, so this never blocks."""
    global _client
    if _client is None:
        s = get_settings()
        _client = MongoClient(s.MONGO_URI, serverSelectionTimeoutMS=s.MONGO_TIMEOUT_MS, tz_aware=True)
    return _client


def configure(client: MongoClient) -> None:
    """Point the module at another client (used by tests)."""
    global _client
    _client = client


def get_db() -> Database:
    """The database named in MONGO_URI (e.g. .../autosound)."""
    return get_client().get_default_database()


def get_collection(name: str) -> Collection:
    return get_db()[name]


def ensure_indexes() -> dict[str, list[str]]:
    """Create every index in INDEXES if missing. Idempotent: re-running creates nothing new."""
    db = get_db()
    return {name: db[name].create_indexes(models) for name, models in INDEXES.items()}


def ping() -> float:
    """Run the ping command and return the round trip in milliseconds. Raises if Mongo is unreachable."""
    start = time.perf_counter()
    get_client().admin.command("ping")
    return round((time.perf_counter() - start) * 1000, 2)
