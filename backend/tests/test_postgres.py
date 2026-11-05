import pytest
from sqlalchemy import Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db import postgres
from app.db.repositories.base import SqlRepo


class WidgetBase(DeclarativeBase):
    """Separate metadata so this test-only table never appears in the app's Base.metadata."""


class Widget(WidgetBase):
    __tablename__ = "test_widgets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    owner_id: Mapped[int] = mapped_column(Integer, default=1)


class WidgetRepo(SqlRepo[Widget]):
    model = Widget


@pytest.fixture
def db(sqlite_engine):
    Widget.__table__.create(sqlite_engine)
    yield sqlite_engine


def count_rows():
    with postgres.session_scope() as s:
        return len(list(s.scalars(select(Widget))))


def test_session_scope_commits(db):
    with postgres.session_scope() as s:
        s.add(Widget(name="a"))
    assert count_rows() == 1


def test_session_scope_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with postgres.session_scope() as s:
            s.add(Widget(name="a"))
            s.flush()
            raise RuntimeError("boom")
    assert count_rows() == 0


def test_session_scope_always_closes(db, monkeypatch):
    closed = []
    real_factory = postgres.get_session_factory()

    def factory():
        session = real_factory()
        original = session.close
        session.close = lambda: (closed.append(True), original())[1]
        return session

    monkeypatch.setattr(postgres, "get_session_factory", lambda: factory)
    with postgres.session_scope():
        pass
    with pytest.raises(ValueError):
        with postgres.session_scope():
            raise ValueError
    assert closed == [True, True]


def test_get_session_dependency_commits(db):
    gen = postgres.get_session()
    s = next(gen)
    s.add(Widget(name="dep"))
    with pytest.raises(StopIteration):
        next(gen)
    assert count_rows() == 1


def test_ping_returns_latency_ms(db):
    ms = postgres.ping()
    assert isinstance(ms, float) and ms >= 0


def test_ping_raises_when_down():
    postgres.configure(postgres.make_engine("postgresql+psycopg2://u:p@127.0.0.1:1/x"))
    with pytest.raises(Exception):
        postgres.ping()


def test_importing_does_not_connect():
    # get_engine is lazy; creating an engine for an unreachable host must not raise.
    postgres.make_engine("postgresql+psycopg2://u:p@10.255.255.1:5432/x")


# ---- SqlRepo -----------------------------------------------------------------------------

def test_crud(db):
    with postgres.session_scope() as s:
        repo = WidgetRepo(s)
        w = repo.add(Widget(name="first"))
        assert w.id is not None
        assert repo.get(w.id).name == "first"
        assert repo.update(w.id, {"name": "renamed"}).name == "renamed"
        assert repo.update(999, {"name": "x"}) is None
        with pytest.raises(AttributeError):
            repo.update(w.id, {"nope": 1})
        assert repo.delete(w.id) is True
        assert repo.delete(w.id) is False
        assert repo.get(w.id) is None


def test_paginate_shape_and_pages(db):
    with postgres.session_scope() as s:
        repo = WidgetRepo(s)
        for i in range(25):
            repo.add(Widget(name=f"w{i:02d}", owner_id=1 if i % 2 == 0 else 2))
        q = select(Widget).order_by(Widget.id)
        p1 = repo.paginate(q, page=1, page_size=10)
        assert set(p1) == {"items", "page", "page_size", "total"}
        assert (p1["page"], p1["page_size"], p1["total"], len(p1["items"])) == (1, 10, 25, 10)
        p3 = repo.paginate(q, page=3, page_size=10)
        assert [w.name for w in p3["items"]] == [f"w{i}" for i in range(20, 25)]
        assert repo.paginate(q, page=4, page_size=10)["items"] == []
        assert repo.paginate(page=1, page_size=5)["total"] == 25   # default query: whole table


def test_paginate_owner_filter_in_query(db):
    with postgres.session_scope() as s:
        repo = WidgetRepo(s)
        for i in range(6):
            repo.add(Widget(name=f"o{i}", owner_id=7 if i < 2 else 8))
        page = repo.paginate(select(Widget).where(Widget.owner_id == 7).order_by(Widget.id), 1, 20)
        assert page["total"] == 2 and all(w.owner_id == 7 for w in page["items"])


@pytest.mark.parametrize("page,size", [(0, 10), (1, 0), (1, 101)])
def test_paginate_rejects_bad_paging(db, page, size):
    with postgres.session_scope() as s:
        with pytest.raises(ValueError):
            WidgetRepo(s).paginate(select(Widget), page, size)
