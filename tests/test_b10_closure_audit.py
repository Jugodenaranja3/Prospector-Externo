from __future__ import annotations

from apps.b10_closure_audit.main import (
    AFFECTED_SOURCE_IDS,
    evaluate_state,
)


def _state(*, mhe="FAILED", sigma="FAILED"):
    sources = {}
    for source_id in AFFECTED_SOURCE_IDS:
        status = "SUCCESS"
        failure_code = None
        if source_id == "mhe":
            status = mhe
            failure_code = (
                "ROBOTS_UNREACHABLE" if status == "FAILED" else None
            )
        elif source_id == "sigma":
            status = sigma
            failure_code = (
                "ROBOTS_UNREACHABLE" if status == "FAILED" else None
            )

        sources[source_id] = {
            "status": status,
            "attempts": 1,
            "summary": {
                "workflow": "javascript",
                "failure_code": failure_code,
                "resources_found": 0,
                "stop_reason": "QUEUE_EXHAUSTED",
            },
        }
    return {"sources": sources}


def test_current_expected_closure_is_closed_with_two_external_blockers():
    report = evaluate_state(_state())

    assert report["closure_status"] == "CLOSED_WITH_EXTERNAL_BLOCKERS"
    assert report["remediation"]["success_count"] == 21
    assert report["remediation"]["accepted_external_blocker_count"] == 2
    assert report["remediation"]["unresolved_count"] == 0
    assert report["reconciled_operational"] == {
        "successful": 39,
        "external_blocked": 2,
        "accounted": 41,
    }


def test_future_success_of_external_sites_is_also_a_valid_closed_state():
    report = evaluate_state(_state(mhe="SUCCESS", sigma="SUCCESS"))

    assert report["closure_status"] == "CLOSED_ALL_SUCCESS"
    assert report["remediation"]["success_count"] == 23
    assert report["remediation"]["accepted_external_blocker_count"] == 0
    assert report["reconciled_operational"]["successful"] == 41


def test_unexpected_failure_keeps_b10_open():
    state = _state()
    state["sources"]["bm"]["status"] = "FAILED"
    state["sources"]["bm"]["summary"]["failure_code"] = "HTTP_500"

    report = evaluate_state(state)

    assert report["closure_status"] == "OPEN"
    assert report["remediation"]["unresolved_count"] == 1
    assert report["remediation"]["unresolved"][0]["source_id"] == "bm"


def test_wrong_failure_code_on_external_source_is_not_silently_accepted():
    state = _state()
    state["sources"]["mhe"]["summary"]["failure_code"] = "UNHANDLED_WORKFLOW_EXCEPTION"

    report = evaluate_state(state)

    assert report["closure_status"] == "OPEN"
    assert report["remediation"]["accepted_external_blocker_count"] == 1
    assert report["remediation"]["unresolved"][0]["source_id"] == "mhe"
