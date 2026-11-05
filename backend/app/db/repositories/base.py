"""Repository base classes: the only code that talks to PostgreSQL or MongoDB (BR-5.1).

Every repository has the same contract: get, add, update, delete, paginate. paginate returns
{"items", "page", "page_size", "total"}, the shape every list endpoint uses.

Owner scoping (BR-1.3): an owner's filter must be part of the query passed to paginate (or of the
repository method), never applied after fetching.

Usage (SQL):
    class CarRepo(SqlRepo[Car]):
        model = Car
        def list_for_owner(self, owner_id, page, page_size):
            return self.paginate(select(Car).where(Car.owner_id == owner_id).order_by(Car.id),
                                 page, page_size)

    with session_scope() as session:
        CarRepo(session).list_for_owner(user.id, 1, 20)

Usage (Mongo):
    class AlertRepo(MongoRepo):
        collection_name = "alerts"

    AlertRepo().paginate({"car_id": {"$in": car_ids}}, page=1, page_size=20,
                         sort=[("timestamp", -1)])
"""
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Generic, TypeVar

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.collection import Collection
from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from app.db import mongo
from app.db.models.base import Base

MAX_PAGE_SIZE = 100

T = TypeVar("T")
M = TypeVar("M", bound=Base)


def page_dict(items: list, page: int, page_size: int, total: int) -> dict[str, Any]:
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def check_paging(page: int, page_size: int) -> None:
    if page < 1:
        raise ValueError("page must be 1 or more")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")


class BaseRepository(ABC, Generic[T]):
    """Common CRUD contract."""

    @abstractmethod
    def get(self, id: Any) -> T | None: ...

    @abstractmethod
    def add(self, obj: T) -> T: ...

    @abstractmethod
    def update(self, id: Any, fields: dict[str, Any]) -> T | None: ...

    @abstractmethod
    def delete(self, id: Any) -> bool: ...

    @abstractmethod
    def paginate(self, query: Any, page: int = 1, page_size: int = 20, **kwargs) -> dict[str, Any]: ...


class SqlRepo(BaseRepository[M]):
    """Repository for one SQLAlchemy model. Set `model` on the subclass.

    Writes only flush; the surrounding session_scope() commits or rolls back.
    """

    model: type[M]

    def __init__(self, session: Session):
        self.session = session

    def get(self, id: Any) -> M | None:
        return self.session.get(self.model, id)

    def add(self, obj: M) -> M:
        self.session.add(obj)
        self.session.flush()          # assigns generated ids, raises constraint errors now
        return obj

    def update(self, id: Any, fields: dict[str, Any]) -> M | None:
        obj = self.get(id)
        if obj is None:
            return None
        for key, value in fields.items():
            if not hasattr(obj, key):
                raise AttributeError(f"{self.model.__name__} has no field {key!r}")
            setattr(obj, key, value)
        self.session.flush()
        return obj

    def delete(self, id: Any) -> bool:
        obj = self.get(id)
        if obj is None:
            return False
        self.session.delete(obj)
        self.session.flush()
        return True

    def paginate(self, query: Select | None = None, page: int = 1, page_size: int = 20, **_) -> dict[str, Any]:
        """Run a SELECT one page at a time. Give the query an ORDER BY for stable pages."""
        check_paging(page, page_size)
        if query is None:
            query = select(self.model)
        total = self.session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        items = list(self.session.scalars(query.limit(page_size).offset((page - 1) * page_size)))
        return page_dict(items, page, page_size, total or 0)

    # Column used for "oldest record" in GET /admin/db/stats (None = not reported).
    time_column: str | None = "created_at"

    def table_stats(self) -> dict[str, Any]:
        """{count, size_bytes, oldest} for this table. size_bytes is PostgreSQL-only (None elsewhere)."""
        table = self.model.__table__
        count = self.session.scalar(select(func.count()).select_from(table)) or 0
        oldest = None
        if self.time_column and self.time_column in table.c:
            oldest = self.session.scalar(select(func.min(table.c[self.time_column])))
        size = None
        if self.session.get_bind().dialect.name == "postgresql":
            size = self.session.scalar(text("SELECT pg_total_relation_size(CAST(:t AS regclass))"),
                                       {"t": table.name})
        return {"count": count, "size_bytes": size, "oldest": oldest}


class MongoRepo(BaseRepository[dict]):
    """Repository for one MongoDB collection. Set `collection_name` on the subclass.

    Documents come back with `id` (string) instead of `_id`, matching the API (alert ids are
    24-character ObjectId strings).
    """

    collection_name: str
    # Timestamp field used for retention and "oldest record" (None = not time-based).
    time_field: str | None = None

    def __init__(self, collection: Collection | None = None):
        self.collection = collection if collection is not None else mongo.get_collection(self.collection_name)

    @staticmethod
    def _oid(id: Any) -> Any:
        if isinstance(id, ObjectId):
            return id
        try:
            return ObjectId(id)
        except (InvalidId, TypeError):
            return id                  # collections may use non-ObjectId ids

    @staticmethod
    def to_public(doc: dict | None) -> dict | None:
        if doc is None:
            return None
        out = {k: v for k, v in doc.items() if k != "_id"}
        if "_id" in doc:
            out["id"] = str(doc["_id"])
        return out

    def get(self, id: Any) -> dict | None:
        return self.to_public(self.collection.find_one({"_id": self._oid(id)}))

    def find_one(self, filter: dict[str, Any]) -> dict | None:
        return self.to_public(self.collection.find_one(filter))

    def add(self, obj: dict) -> dict:
        doc = dict(obj)
        result = self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return self.to_public(doc)

    def update(self, id: Any, fields: dict[str, Any]) -> dict | None:
        doc = self.collection.find_one_and_update(
            {"_id": self._oid(id)}, {"$set": fields}, return_document=ReturnDocument.AFTER
        )
        return self.to_public(doc)

    def delete(self, id: Any) -> bool:
        return self.collection.delete_one({"_id": self._oid(id)}).deleted_count > 0

    def paginate(
        self,
        query: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: list[tuple[str, int]] | None = None,
        **_,
    ) -> dict[str, Any]:
        """Find one page of documents matching `query` (a Mongo filter)."""
        check_paging(page, page_size)
        query = query or {}
        total = self.collection.count_documents(query)
        cursor = self.collection.find(query)
        if sort:
            cursor = cursor.sort(sort)
        cursor = cursor.skip((page - 1) * page_size).limit(page_size)
        return page_dict([self.to_public(d) for d in cursor], page, page_size, total)

    # ---- maintenance helpers (GET /admin/db/stats, POST /admin/db/retention/run) ----------

    def _older_than(self, cutoff: datetime) -> dict[str, Any]:
        if not self.time_field:
            raise ValueError(f"{type(self).__name__} has no time_field")
        return {self.time_field: {"$lt": cutoff}}

    def count_older_than(self, cutoff: datetime) -> int:
        return self.collection.count_documents(self._older_than(cutoff))

    def iter_older_than(self, cutoff: datetime, batch_size: int = 1000) -> Iterator[dict]:
        """Raw documents (with _id) older than cutoff, oldest first; for archiving."""
        return self.collection.find(self._older_than(cutoff)).sort([(self.time_field, 1)]).batch_size(batch_size)

    def delete_ids(self, ids: list) -> int:
        """Delete exactly these documents (already archived); returns how many were removed."""
        if not ids:
            return 0
        return self.collection.delete_many({"_id": {"$in": list(ids)}}).deleted_count

    def collection_stats(self) -> dict[str, Any]:
        """{count, size_bytes, oldest}. size_bytes comes from $collStats (None if the server or
        a test double does not support it)."""
        count = self.collection.count_documents({})
        oldest = None
        if self.time_field:
            first = next(iter(self.collection.find({}, {self.time_field: 1})
                              .sort([(self.time_field, 1)]).limit(1)), None)
            oldest = first.get(self.time_field) if first else None
        size = None
        try:
            stats = next(iter(self.collection.aggregate([{"$collStats": {"storageStats": {}}}])), None)
            if stats:
                size = stats.get("storageStats", {}).get("size")
        except Exception:  # noqa: BLE001 – stats are best effort
            size = None
        return {"count": count, "size_bytes": size, "oldest": oldest}
