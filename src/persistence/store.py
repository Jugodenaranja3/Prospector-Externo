from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from .model import validate_checkpoint


class CheckpointStore(Protocol):
    def save(self, checkpoint: dict[str, Any]) -> None:
        ...

    def load(self, run_id: str | None = None) -> dict[str, Any]:
        ...


class JsonCheckpointStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, checkpoint: dict[str, Any]) -> None:
        validate_checkpoint(checkpoint)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(checkpoint, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def load(self, run_id: str | None = None) -> dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Checkpoint JSON inválido")
        validate_checkpoint(value)

        if run_id is not None and value.get("run_id") != run_id:
            raise ValueError(
                f"run_id solicitado {run_id} no coincide con "
                f"{value.get('run_id')}"
            )
        return value


class MongoCheckpointStore:
    def __init__(
        self,
        uri: str,
        database: str,
        collection: str = "prospector_run_checkpoints",
    ):
        self.uri = uri
        self.database = database
        self.collection = collection

    def _collection(self):
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise RuntimeError(
                "Mongo backend requiere pymongo. "
                "Instala requirements-persistence.txt."
            ) from exc

        client = MongoClient(
            self.uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        )
        client.admin.command("ping")
        return client, client[self.database][self.collection]

    def save(self, checkpoint: dict[str, Any]) -> None:
        validate_checkpoint(checkpoint)
        client, collection = self._collection()
        try:
            document = dict(checkpoint)
            document["_id"] = checkpoint["run_id"]
            collection.replace_one(
                {"_id": checkpoint["run_id"]},
                document,
                upsert=True,
            )
        finally:
            client.close()

    def load(self, run_id: str | None = None) -> dict[str, Any]:
        client, collection = self._collection()
        try:
            if run_id is not None:
                document = collection.find_one({"_id": run_id})
            else:
                document = collection.find_one(
                    {},
                    sort=[("updated_at_utc", -1)],
                )
        finally:
            client.close()

        if document is None:
            raise FileNotFoundError(
                run_id or "latest Mongo checkpoint"
            )

        document.pop("_id", None)
        validate_checkpoint(document)
        return document
