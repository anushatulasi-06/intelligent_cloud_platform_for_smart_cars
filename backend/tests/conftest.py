"""Test fixtures: in-memory SQLite stands in for PostgreSQL, mongomock for MongoDB."""
import mongomock
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.init_db import create_all


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _enable_foreign_keys(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    yield eng
    eng.dispose()


@pytest.fixture
def mongo_db():
    return mongomock.MongoClient()["smartcar_test"]


@pytest.fixture
def db(engine, mongo_db):
    create_all(engine, mongo_db)
    with Session(engine) as session:
        yield session
