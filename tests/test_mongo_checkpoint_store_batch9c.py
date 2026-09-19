from __future__ import annotations

from src.persistence.model import new_checkpoint
from src.persistence.store import MongoCheckpointStore


def fixture_plan():
    rows = []
    for i in range(52):
        operational = i < 41
        rows.append(
            {
                "source_id": f"s{i}",
                "logical_code": f"S{i}",
                "effective_entrypoint": f"https://example.org/{i}",
                "operational_status": (
                    "OPERATIONAL_HTTP_HTML"
                    if operational
                    else "NO_PUBLIC_DATA_EVIDENCE"
                ),
                "workflow_strategy": "html" if operational else None,
                "operational_config": None,
                "next_phase": (
                    "OPERATIONAL_CONFIG"
                    if operational
                    else "B10_STATUS"
                ),
            }
        )
    return {"sources": rows}


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeCollection:
    def __init__(self):
        self.docs = {}
        self.replace_calls = []

    def replace_one(self, query, document, upsert=False):
        self.replace_calls.append((query, document, upsert))
        self.docs[document["_id"]] = dict(document)

    def find_one(self, query=None, sort=None):
        if query and "_id" in query:
            value = self.docs.get(query["_id"])
            return dict(value) if value is not None else None

        if not self.docs:
            return None

        values = list(self.docs.values())
        values.sort(
            key=lambda row: row.get("updated_at_utc") or "",
            reverse=True,
        )
        return dict(values[0])


def test_mongo_store_save_uses_run_id_as_id_and_upsert(monkeypatch):
    checkpoint = new_checkpoint(
        fixture_plan(),
        run_id="mongo-contract-1",
    )

    client = FakeClient()
    collection = FakeCollection()
    store = MongoCheckpointStore(
        uri="mongodb://unused",
        database="unused",
    )

    monkeypatch.setattr(
        store,
        "_collection",
        lambda: (client, collection),
    )

    store.save(checkpoint)

    assert collection.replace_calls
    query, document, upsert = collection.replace_calls[0]
    assert query == {"_id": "mongo-contract-1"}
    assert document["_id"] == "mongo-contract-1"
    assert upsert is True
    assert client.closed is True


def test_mongo_store_load_by_run_id_removes_mongo_id(monkeypatch):
    checkpoint = new_checkpoint(
        fixture_plan(),
        run_id="mongo-contract-2",
    )

    client = FakeClient()
    collection = FakeCollection()
    document = dict(checkpoint)
    document["_id"] = checkpoint["run_id"]
    collection.docs[checkpoint["run_id"]] = document

    store = MongoCheckpointStore(
        uri="mongodb://unused",
        database="unused",
    )
    monkeypatch.setattr(
        store,
        "_collection",
        lambda: (client, collection),
    )

    loaded = store.load("mongo-contract-2")

    assert loaded["run_id"] == "mongo-contract-2"
    assert "_id" not in loaded
    assert client.closed is True


def test_mongo_store_load_latest(monkeypatch):
    collection = FakeCollection()

    older = new_checkpoint(
        fixture_plan(),
        run_id="older",
    )
    older["updated_at_utc"] = "2026-01-01T00:00:00+00:00"
    older_doc = dict(older)
    older_doc["_id"] = "older"
    collection.docs["older"] = older_doc

    newer = new_checkpoint(
        fixture_plan(),
        run_id="newer",
    )
    newer["updated_at_utc"] = "2026-09-18T23:00:00+00:00"
    newer_doc = dict(newer)
    newer_doc["_id"] = "newer"
    collection.docs["newer"] = newer_doc

    client = FakeClient()
    store = MongoCheckpointStore(
        uri="mongodb://unused",
        database="unused",
    )
    monkeypatch.setattr(
        store,
        "_collection",
        lambda: (client, collection),
    )

    loaded = store.load()

    assert loaded["run_id"] == "newer"
    assert client.closed is True
