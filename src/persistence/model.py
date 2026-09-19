from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "prospector-checkpoint-1.0"

PENDING = "PENDING"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
SKIPPED_STATUS = "SKIPPED_STATUS"

VALID_STATES = {
    PENDING,
    RUNNING,
    SUCCEEDED,
    FAILED,
    SKIPPED_STATUS,
}

ALLOWED_TRANSITIONS = {
    PENDING: {RUNNING, SKIPPED_STATUS},
    RUNNING: {SUCCEEDED, FAILED},
    FAILED: {RUNNING},
    SUCCEEDED: set(),
    SKIPPED_STATUS: set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_fingerprint(source: dict[str, Any]) -> str:
    relevant = {
        "source_id": source.get("source_id"),
        "logical_code": source.get("logical_code"),
        "effective_entrypoint": source.get("effective_entrypoint"),
        "operational_status": source.get("operational_status"),
        "workflow_strategy": source.get("workflow_strategy"),
        "operational_config": source.get("operational_config"),
        "next_phase": source.get("next_phase"),
    }
    return stable_hash(relevant)


def plan_fingerprint(plan: dict[str, Any]) -> str:
    rows = plan.get("sources")
    if not isinstance(rows, list):
        raise ValueError("Plan sin sources")
    return stable_hash(
        [
            {
                "source_id": row.get("source_id"),
                "fingerprint": source_fingerprint(row),
            }
            for row in rows
            if isinstance(row, dict)
        ]
    )


def new_checkpoint(
    plan: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    rows = plan.get("sources")
    if not isinstance(rows, list) or len(rows) != 52:
        raise ValueError("El plan operacional debe contener 52 fuentes")

    source_states: dict[str, dict[str, Any]] = {}
    operational = 0
    terminal_status = 0

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Plan contiene source inválida")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("Source sin source_id")

        next_phase = row.get("next_phase")
        if next_phase == "OPERATIONAL_CONFIG":
            state = PENDING
            operational += 1
        elif next_phase == "B10_STATUS":
            state = SKIPPED_STATUS
            terminal_status += 1
        else:
            raise ValueError(
                f"{source_id}: next_phase no cerrado antes de B9: {next_phase}"
            )

        source_states[source_id] = {
            "source_id": source_id,
            "logical_code": row.get("logical_code"),
            "state": state,
            "attempts": 0,
            "source_fingerprint": source_fingerprint(row),
            "started_at_utc": None,
            "finished_at_utc": utc_now() if state == SKIPPED_STATUS else None,
            "last_error": None,
            "resource_count": None,
            "metadata": {},
        }

    now = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or str(uuid.uuid4()),
        "created_at_utc": now,
        "updated_at_utc": now,
        "plan_fingerprint": plan_fingerprint(plan),
        "logical_sources": len(source_states),
        "operational_sources": operational,
        "status_only_sources": terminal_status,
        "sources": source_states,
    }


def validate_checkpoint(checkpoint: dict[str, Any]) -> None:
    if checkpoint.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema_version de checkpoint no soportado")

    sources = checkpoint.get("sources")
    if not isinstance(sources, dict) or len(sources) != 52:
        raise ValueError("Checkpoint debe contener 52 sources")

    for source_id, row in sources.items():
        if not isinstance(row, dict):
            raise ValueError(f"{source_id}: estado inválido")
        state = row.get("state")
        if state not in VALID_STATES:
            raise ValueError(f"{source_id}: state inválido {state}")


def transition(
    checkpoint: dict[str, Any],
    source_id: str,
    new_state: str,
    *,
    error: str | None = None,
    resource_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_checkpoint(checkpoint)

    if new_state not in VALID_STATES:
        raise ValueError(f"Estado destino inválido: {new_state}")

    if source_id not in checkpoint["sources"]:
        raise KeyError(source_id)

    updated = deepcopy(checkpoint)
    row = updated["sources"][source_id]
    old_state = row["state"]

    if new_state not in ALLOWED_TRANSITIONS[old_state]:
        raise ValueError(
            f"Transición inválida {source_id}: {old_state} -> {new_state}"
        )

    now = utc_now()
    row["state"] = new_state

    if new_state == RUNNING:
        row["attempts"] = int(row.get("attempts") or 0) + 1
        row["started_at_utc"] = now
        row["finished_at_utc"] = None
        row["last_error"] = None

    elif new_state == SUCCEEDED:
        row["finished_at_utc"] = now
        row["last_error"] = None
        row["resource_count"] = resource_count

    elif new_state == FAILED:
        row["finished_at_utc"] = now
        row["last_error"] = error or "unspecified_error"

    elif new_state == SKIPPED_STATUS:
        row["finished_at_utc"] = now

    if metadata:
        current = row.get("metadata")
        if not isinstance(current, dict):
            current = {}
        current.update(metadata)
        row["metadata"] = current

    updated["updated_at_utc"] = now
    validate_checkpoint(updated)
    return updated


def recover_interrupted(
    checkpoint: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    validate_checkpoint(checkpoint)

    updated = deepcopy(checkpoint)
    recovered: list[str] = []
    now = utc_now()

    for source_id, row in updated["sources"].items():
        if row.get("state") != RUNNING:
            continue
        row["state"] = FAILED
        row["finished_at_utc"] = now
        row["last_error"] = "interrupted_previous_run"
        recovered.append(source_id)

    if recovered:
        updated["updated_at_utc"] = now

    return updated, recovered


def resume_candidates(
    checkpoint: dict[str, Any],
    *,
    include_failed: bool = True,
) -> list[str]:
    validate_checkpoint(checkpoint)

    allowed = {PENDING}
    if include_failed:
        allowed.add(FAILED)

    return [
        source_id
        for source_id, row in checkpoint["sources"].items()
        if row.get("state") in allowed
    ]


def summary(checkpoint: dict[str, Any]) -> dict[str, int]:
    validate_checkpoint(checkpoint)
    counts = {state: 0 for state in sorted(VALID_STATES)}
    for row in checkpoint["sources"].values():
        counts[row["state"]] += 1
    return counts


def assert_plan_compatible(
    checkpoint: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    validate_checkpoint(checkpoint)
    current = plan_fingerprint(plan)
    expected = checkpoint.get("plan_fingerprint")
    if current != expected:
        raise ValueError(
            "El plan operacional cambió desde que se creó el checkpoint; "
            "no es seguro reanudar el mismo run_id."
        )
