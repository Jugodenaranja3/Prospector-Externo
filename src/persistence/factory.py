from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .store import JsonCheckpointStore, MongoCheckpointStore


def load_config(path: str | Path) -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("checkpointing config inválida")
    return value


def create_store(
    config: dict[str, Any],
    *,
    backend: str | None = None,
):
    selected = (
        backend
        or os.getenv(config.get("backend_env", "PROSPECTOR_CHECKPOINT_BACKEND"))
        or config.get("default_backend")
        or "json"
    ).lower()

    if selected == "json":
        json_cfg = config.get("json") or {}
        return JsonCheckpointStore(
            json_cfg.get("path")
            or ".runtime/checkpoints/latest.json"
        )

    if selected == "mongo":
        mongo_cfg = config.get("mongo") or {}

        uri_env = mongo_cfg.get(
            "uri_env",
            "PROSPECTOR_MONGO_URI",
        )
        database_env = mongo_cfg.get(
            "database_env",
            "PROSPECTOR_MONGO_DATABASE",
        )

        uri = os.getenv(uri_env)
        database = os.getenv(database_env)

        if not uri:
            raise RuntimeError(
                f"Mongo backend requiere variable {uri_env}"
            )
        if not database:
            raise RuntimeError(
                f"Mongo backend requiere variable {database_env}"
            )

        return MongoCheckpointStore(
            uri=uri,
            database=database,
            collection=mongo_cfg.get(
                "collection",
                "prospector_run_checkpoints",
            ),
        )

    raise ValueError(f"Backend de checkpoint no soportado: {selected}")
