"""PostgreSQL: engine, session factory, transaction scope and health ping.

Only repositories (app/db/repositories/) should use sessions to query.

Usage:
    from app.db.postgres import session_scope
    with session_scope() as session:
        UserRepo(session).add(user)        # committed on exit, rolled back on error

    # FastAPI dependency
    def route(session: Session = Depends(get_session)): ...
"""
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def make_engine(url: str) -> Engine:
    """Create an engine. Kept separate so tests can build one for another URL."""
    kwargs = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs["connect_args"] = {"connect_timeout": get_settings().DB_CONNECT_TIMEOUT_S}
    return create_engine(url, **kwargs)


def get_engine() -> Engine:
    """The app engine, created on first use (importing this module does not connect)."""
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().DATABASE_URL)
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def configure(engine: Engine) -> None:
    """Point the module at another engine (used by tests)."""
    global _engine, _SessionLocal
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit on success, roll back on any error, always close (BR-5.2)."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: a session in its own transaction for the request."""
    with session_scope() as session:
        yield session


def ping() -> float:
    """Run SELECT 1 and return the round trip in milliseconds. Raises if Postgres is unreachable."""
    start = time.perf_counter()
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return round((time.perf_counter() - start) * 1000, 2)
