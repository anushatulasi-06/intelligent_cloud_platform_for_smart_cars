import pytest
from bson import ObjectId

from app.db import mongo
from app.db.repositories.base import MongoRepo
from tests.fakes import FakeCollection


class AlertRepo(MongoRepo):
    collection_name = "alerts"


@pytest.fixture
def repo():
    return AlertRepo(FakeCollection())


def test_add_get_returns_string_id(repo):
    a = repo.add({"car_id": 1, "status": "new"})
    assert isinstance(a["id"], str) and len(a["id"]) == 24 and "_id" not in a
    assert repo.get(a["id"]) == a
    assert repo.get(ObjectId(a["id"])) == a
    assert repo.get("not-an-object-id") is None


def test_update_and_delete(repo):
    a = repo.add({"car_id": 1, "status": "new"})
    assert repo.update(a["id"], {"status": "acknowledged"})["status"] == "acknowledged"
    assert repo.update(str(ObjectId()), {"status": "x"}) is None
    assert repo.delete(a["id"]) is True
    assert repo.delete(a["id"]) is False


def test_paginate_filter_sort(repo):
    for i in range(23):
        repo.add({"car_id": 1 if i % 3 else 2, "timestamp": i})
    p = repo.paginate({"car_id": {"$in": [1]}}, page=2, page_size=5, sort=[("timestamp", -1)])
    assert set(p) == {"items", "page", "page_size", "total"}
    assert p["total"] == 15 and p["page"] == 2 and p["page_size"] == 5
    assert [d["timestamp"] for d in p["items"]] == [14, 13, 11, 10, 8]
    assert all(d["car_id"] == 1 for d in p["items"])


def test_paginate_rejects_bad_paging(repo):
    with pytest.raises(ValueError):
        repo.paginate({}, page=0, page_size=5)


class _FakeDb(dict):
    def __missing__(self, name):
        self[name] = FakeCollection()
        return self[name]


def test_ensure_indexes_idempotent(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(mongo, "get_db", lambda: db)
    first = mongo.ensure_indexes()
    second = mongo.ensure_indexes()
    assert first == second
    assert set(first) == {"alerts", "device_data_records", "car_positions"}
    assert len(db["alerts"].indexes) == 4                       # no duplicates after two runs
    assert db["alerts"].indexes["record_id_unique"]["unique"] is True
    assert db["device_data_records"].indexes["clip_id_unique"]["sparse"] is True


def test_ping_reports_latency(monkeypatch):
    class Admin:
        def command(self, name):
            assert name == "ping"
            return {"ok": 1}

    class Client:
        admin = Admin()

    monkeypatch.setattr(mongo, "get_client", lambda: Client())
    assert mongo.ping() >= 0


def test_ping_raises_when_down():
    from pymongo import MongoClient
    mongo.configure(MongoClient("mongodb://127.0.0.1:1/x", serverSelectionTimeoutMS=200))
    with pytest.raises(Exception):
        mongo.ping()
    mongo.configure(None)
