"""Test setup: no real PostgreSQL, MongoDB or AWS is needed.

Environment is set before the app is imported so a developer's backend/.env cannot point tests at
real services.
"""
import os

os.environ.update({
    "DATABASE_URL": "sqlite+pysqlite:///:memory:",
    "MONGO_URI": "mongodb://localhost:27017/autosound_test",
    "S3_BUCKET": "test-bucket",
    "AWS_REGION": "us-west-2",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "CORS_ORIGINS": "http://localhost:5173",
    "MONGO_ENSURE_INDEXES_ON_STARTUP": "false",
    "FAIL_INTERRUPTED_JOBS_ON_STARTUP": "false",
    "LOAD_MODEL_ON_STARTUP": "false",
    "DEVICE_SWEEPER_ENABLED": "false",
    "PROCESSING_WORKER_ENABLED": "false",
    "LOCAL_AUDIO_DIR": "",          # audio links are presigned S3 URLs unless a test opts in
})

import pytest  # noqa: E402
import mongomock  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import postgres  # noqa: E402
from app.db.models import Base  # noqa: E402

get_settings.cache_clear()


@pytest.fixture
def sqlite_engine():
    """In-memory SQLite shared across connections, wired into app.db.postgres."""
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    postgres.configure(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sql_db(sqlite_engine):
    """All tables created on SQLite with foreign keys enforced (ON DELETE CASCADE works)."""
    @event.listens_for(sqlite_engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
    with sqlite_engine.connect() as conn:          # StaticPool: the one connection already exists
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(sqlite_engine)
    yield sqlite_engine


@pytest.fixture
def session(sql_db):
    with postgres.session_scope() as s:
        yield s


@pytest.fixture
def mongo_db():
    """In-memory MongoDB database."""
    return mongomock.MongoClient(tz_aware=True)["autosound_test"]
