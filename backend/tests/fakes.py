"""Minimal in-memory stand-in for a pymongo Collection (equality and $in filters, sort,
skip, limit). Enough to test MongoRepo without a running MongoDB."""
from copy import deepcopy

from bson import ObjectId
from pymongo import ReturnDocument


class _Result:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _matches(doc, query):
    for key, cond in query.items():
        value = doc.get(key)
        if isinstance(cond, dict) and "$in" in cond:
            if value not in cond["$in"]:
                return False
        elif value != cond:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, spec):
        for key, direction in reversed(spec):
            self.docs.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def skip(self, n):
        self.docs = self.docs[n:]
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def __iter__(self):
        return iter(deepcopy(self.docs))


class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.indexes: dict[str, object] = {}

    def insert_one(self, doc):
        doc = deepcopy(doc)
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return _Result(inserted_id=doc["_id"])

    def find_one(self, query):
        return next((deepcopy(d) for d in self.docs if _matches(d, query)), None)

    def find(self, query):
        return FakeCursor([d for d in self.docs if _matches(d, query)])

    def count_documents(self, query):
        return sum(1 for d in self.docs if _matches(d, query))

    def find_one_and_update(self, query, update, return_document=ReturnDocument.BEFORE):
        for d in self.docs:
            if _matches(d, query):
                d.update(update["$set"])
                return deepcopy(d)
        return None

    def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _matches(d, query):
                del self.docs[i]
                return _Result(deleted_count=1)
        return _Result(deleted_count=0)

    def create_indexes(self, models):
        names = []
        for m in models:
            name = m.document["name"]
            self.indexes.setdefault(name, m.document)   # existing index: no-op, like MongoDB
            names.append(name)
        return names
