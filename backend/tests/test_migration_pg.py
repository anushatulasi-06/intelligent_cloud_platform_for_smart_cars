"""Alembic migration against real PostgreSQL. Skipped unless TEST_DATABASE_URL is set, e.g.

    TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/autosound_test python -m pytest tests/test_migration_pg.py

WARNING: drops and recreates everything in that database. Never point it at real data.
"""
import os
from decimal import Decimal

import mongomock
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import BACKEND_DIR
from app.db import postgres

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set (needs a throwaway PostgreSQL)")


@pytest.fixture(scope="module")
def alembic_cfg():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "app/db/migrations"))
    return cfg


@pytest.fixture(scope="module")
def engine(alembic_cfg):
    os.environ["DATABASE_URL"] = URL
    from app.config import get_settings
    get_settings.cache_clear()
    eng = create_engine(URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(alembic_cfg, "head")
    postgres.configure(eng)
    yield eng
    eng.dispose()


def test_upgrade_matches_models_exactly(engine):
    from app.db.models import Base
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True,
                                                                        "compare_server_default": True}),
                                Base.metadata)
    assert diff == [], f"migration and models differ: {diff}"


def test_postgres_types(engine):
    cols = {(t, c["name"]): c["type"] for t in inspect(engine).get_table_names()
            for c in inspect(engine).get_columns(t)}
    assert str(cols[("maintenance_jobs", "params")]) == "JSONB"
    assert str(cols[("maintenance_jobs", "id")]) == "UUID"
    assert str(cols[("devices", "wifi_password_enc")]) == "BYTEA"
    assert str(cols[("cars", "last_lat")]) == "DOUBLE PRECISION"
    assert str(cols[("devices", "device_key_hash")]) == "CHAR(64)"
    assert str(cols[("users", "created_at")]) == "TIMESTAMP"          # reflected; tz checked below
    with engine.connect() as conn:
        tz = conn.execute(text("SELECT data_type FROM information_schema.columns "
                               "WHERE table_name='users' AND column_name='created_at'")).scalar()
    assert tz == "timestamp with time zone"


def test_partial_unique_index(engine):
    with engine.connect() as conn:
        ddl = conn.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname='uq_model_versions_one_active'")).scalar()
    assert "UNIQUE" in ddl and "WHERE is_active" in ddl
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO model_versions (version_label, is_active) VALUES ('x1', true), ('x2', false), ('x3', false)"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO model_versions (version_label, is_active) VALUES ('x4', true)"))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM model_versions WHERE version_label LIKE 'x%'"))


@pytest.mark.parametrize("sql", [
    "INSERT INTO users (name, email, role, password_hash) VALUES ('a','bad-role@x','admin','h')",
    "INSERT INTO model_versions (version_label, confidence_threshold) VALUES ('t', 0.49)",
    "INSERT INTO model_versions (version_label, confidence_threshold) VALUES ('t', 0.96)",
    "INSERT INTO alert_type_config (alert_type, default_severity) VALUES ('human', 'urgent')",
    "INSERT INTO maintenance_jobs (id, job_type, status) VALUES (gen_random_uuid(), 'vacuum', 'queued')",
])
def test_check_constraints(engine, sql):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(sql))


def test_seed_twice_on_postgres(engine):
    from sqlalchemy import func, select

    from app.db.models import ModelVersion, User
    from app.db.seed import seed_mongo, seed_postgres
    mdb = mongomock.MongoClient(tz_aware=True)["autosound"]
    for _ in range(2):
        with postgres.session_scope() as s:
            res, cars, mics = seed_postgres(s)
        seed_mongo(mdb, cars, mics, res)
    with postgres.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(User)) == 4
        active = s.scalar(select(ModelVersion).where(ModelVersion.is_active.is_(True)))
        assert active.version_label == "cnn-v1" and active.confidence_threshold == Decimal("0.75")
    assert mdb["alerts"].count_documents({}) == 25


def test_downgrade_to_base_and_back(engine, alembic_cfg):
    command.downgrade(alembic_cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(alembic_cfg, "head")
    assert "model_versions" in inspect(engine).get_table_names()
