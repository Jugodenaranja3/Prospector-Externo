from __future__ import annotations

from pathlib import Path

from src.execution.checkpointed_batch import (
    match_source_entry,
    validate_source_mapping,
)


def test_execution_map_resolves_shared_physical_config():
    plan_source = {
        "source_id": "asfi_bcb",
        "logical_code": "ASFI - BCB",
        "effective_entrypoint": "https://www.bcb.gob.bo",
    }
    config = {
        "sources": [
            {
                "source_id": "bcb",
                "entrypoint": "https://www.bcb.gob.bo",
            }
        ]
    }
    execution_map = {
        "sources": [
            {
                "logical_source_id": "asfi_bcb",
                "config_source_id": "bcb",
            }
        ]
    }

    _, entry = match_source_entry(
        plan_source,
        config,
        execution_map,
    )
    assert entry["source_id"] == "bcb"


def test_validate_uses_execution_map_for_multiple_logical_sources():
    rows = []
    for i in range(52):
        operational = i < 41
        rows.append(
            {
                "source_id": f"s{i}",
                "logical_code": f"S{i}",
                "effective_entrypoint": "https://example.org",
                "next_phase": (
                    "OPERATIONAL_CONFIG"
                    if operational
                    else "B10_STATUS"
                ),
            }
        )

    plan = {"sources": rows}
    config = {
        "sources": [
            {
                "source_id": "physical",
                "entrypoint": "https://example.org",
            }
        ]
    }
    execution_map = {
        "sources": [
            {
                "logical_source_id": f"s{i}",
                "config_source_id": "physical",
            }
            for i in range(41)
        ]
    }

    report = validate_source_mapping(
        plan,
        config,
        execution_map,
    )
    assert report["matched_sources"] == 41
    assert report["unmatched_sources"] == 0
