from __future__ import annotations

from pathlib import Path

import pytest

from src.persistence.model import (
    FAILED,
    PENDING,
    RUNNING,
    SKIPPED_STATUS,
    SUCCEEDED,
    assert_plan_compatible,
    new_checkpoint,
    recover_interrupted,
    resume_candidates,
    summary,
    transition,
)
from src.persistence.store import JsonCheckpointStore


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


def test_new_checkpoint_has_41_pending_and_11_status():
    checkpoint = new_checkpoint(fixture_plan(), run_id="run-1")
    counts = summary(checkpoint)
    assert counts[PENDING] == 41
    assert counts[SKIPPED_STATUS] == 11


def test_success_transition_tracks_attempts_and_resources():
    checkpoint = new_checkpoint(fixture_plan(), run_id="run-1")
    checkpoint = transition(checkpoint, "s0", RUNNING)
    checkpoint = transition(
        checkpoint,
        "s0",
        SUCCEEDED,
        resource_count=12,
    )
    assert checkpoint["sources"]["s0"]["attempts"] == 1
    assert checkpoint["sources"]["s0"]["resource_count"] == 12


def test_failed_source_can_retry():
    checkpoint = new_checkpoint(fixture_plan(), run_id="run-1")
    checkpoint = transition(checkpoint, "s0", RUNNING)
    checkpoint = transition(
        checkpoint,
        "s0",
        FAILED,
        error="boom",
    )
    checkpoint = transition(checkpoint, "s0", RUNNING)
    assert checkpoint["sources"]["s0"]["attempts"] == 2


def test_interrupted_running_is_recovered_as_failed():
    checkpoint = new_checkpoint(fixture_plan(), run_id="run-1")
    checkpoint = transition(checkpoint, "s0", RUNNING)

    recovered_checkpoint, recovered = recover_interrupted(checkpoint)

    assert recovered == ["s0"]
    assert recovered_checkpoint["sources"]["s0"]["state"] == FAILED
    assert (
        recovered_checkpoint["sources"]["s0"]["last_error"]
        == "interrupted_previous_run"
    )


def test_resume_candidates_include_pending_and_failed_only():
    checkpoint = new_checkpoint(fixture_plan(), run_id="run-1")
    checkpoint = transition(checkpoint, "s0", RUNNING)
    checkpoint = transition(checkpoint, "s0", FAILED, error="boom")
    checkpoint = transition(checkpoint, "s1", RUNNING)
    checkpoint = transition(checkpoint, "s1", SUCCEEDED)

    candidates = resume_candidates(checkpoint)
    assert "s0" in candidates
    assert "s1" not in candidates
    assert "s41" not in candidates


def test_json_store_is_atomic_and_roundtrips(tmp_path: Path):
    checkpoint = new_checkpoint(fixture_plan(), run_id="run-1")
    store = JsonCheckpointStore(tmp_path / "checkpoint.json")
    store.save(checkpoint)
    loaded = store.load("run-1")
    assert loaded["run_id"] == "run-1"
    assert loaded["sources"]["s0"]["state"] == PENDING
    assert not (tmp_path / "checkpoint.json.tmp").exists()


def test_plan_drift_is_rejected():
    plan = fixture_plan()
    checkpoint = new_checkpoint(plan, run_id="run-1")
    changed = fixture_plan()
    changed["sources"][0]["effective_entrypoint"] = "https://changed.example.org"

    with pytest.raises(ValueError, match="plan operacional cambió"):
        assert_plan_compatible(checkpoint, changed)


def test_terminal_success_cannot_restart():
    checkpoint = new_checkpoint(fixture_plan(), run_id="run-1")
    checkpoint = transition(checkpoint, "s0", RUNNING)
    checkpoint = transition(checkpoint, "s0", SUCCEEDED)

    with pytest.raises(ValueError, match="Transición inválida"):
        transition(checkpoint, "s0", RUNNING)
