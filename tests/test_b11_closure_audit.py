from __future__ import annotations

from apps.b11_closure_audit.main import evaluate


def _row(source_id, classification, **extra):
    row = {
        "logical_source_id": source_id,
        "classification": classification,
        "evidence_origin": "baseline",
        "raw_resource_count": 1,
        "legacy_projection_records": 1,
    }
    row.update(extra)
    return row


def _audit():
    rows = []

    for i in range(38):
        source_id = f"ready_{i:02d}"
        rows.append(_row(source_id, "DATAX_READY"))

    # Replace one synthetic ready row with real OMC semantics.
    rows[0] = _row(
        "omc",
        "DATAX_READY",
        evidence_origin="b11_refresh",
        raw_resource_count=90,
        legacy_projection_records=45,
    )

    rows.append(
        _row(
            "transtats",
            "ACQUISITION_JOB_READY",
            legacy_projection_records=0,
            special_downstream_decision="ACQUISITION_JOB_READY",
            special_downstream_manifest="x/acquisition_job.json",
        )
    )

    rows.append(
        _row(
            "mhe",
            "EXTERNAL_BLOCKER",
            raw_resource_count=0,
            legacy_projection_records=0,
        )
    )
    rows.append(
        _row(
            "sigma",
            "EXTERNAL_BLOCKER",
            raw_resource_count=0,
            legacy_projection_records=0,
        )
    )

    return {
        "scope": {"operational_sources": 41},
        "summary": {
            "classification_counts": {
                "DATAX_READY": 38,
                "ACQUISITION_JOB_READY": 1,
                "RAW_READY_NO_PROJECTION": 0,
                "SUCCESS_EMPTY": 0,
                "EXTERNAL_BLOCKER": 2,
                "EXECUTION_NOT_SUCCESS": 0,
                "EVIDENCE_MISSING": 0,
            },
            "total_raw_resources": 6868,
            "total_legacy_projection_records": 5407,
            "total_high_priority_resources": 2169,
            "total_medium_priority_resources": 4698,
        },
        "sources": rows,
    }


def test_expected_b11_state_closes():
    report = evaluate(_audit())

    assert report["closure_status"] == "CLOSED_WITH_EXTERNAL_BLOCKERS"
    assert report["operational"]["accounted"] == 41
    assert report["failed_checks"] == []


def test_omc_must_use_b11_refresh():
    audit = _audit()
    for row in audit["sources"]:
        if row["logical_source_id"] == "omc":
            row["evidence_origin"] = "baseline"

    report = evaluate(audit)

    assert report["closure_status"] == "OPEN"
    assert "omc_datax_ready" in report["failed_checks"]


def test_transtats_must_keep_acquisition_semantics():
    audit = _audit()
    for row in audit["sources"]:
        if row["logical_source_id"] == "transtats":
            row["special_downstream_decision"] = None

    report = evaluate(audit)

    assert report["closure_status"] == "OPEN"
    assert "transtats_acquisition_semantics" in report["failed_checks"]


def test_unresolved_projection_keeps_b11_open():
    audit = _audit()
    audit["summary"]["classification_counts"]["DATAX_READY"] = 37
    audit["summary"]["classification_counts"][
        "RAW_READY_NO_PROJECTION"
    ] = 1

    for row in audit["sources"]:
        if row["logical_source_id"] == "ready_01":
            row["classification"] = "RAW_READY_NO_PROJECTION"

    report = evaluate(audit)

    assert report["closure_status"] == "OPEN"
    assert report["unresolved"] == {
        "ready_01": "RAW_READY_NO_PROJECTION"
    }
