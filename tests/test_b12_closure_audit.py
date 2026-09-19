from __future__ import annotations

from apps.b12_closure_audit.main import evaluate


def _enterprise_report():
    return {
        "status": "ENTERPRISE_PACKAGE_READY",
    }


def _fresh():
    return {
        "status": "ENTERPRISE_PACKAGE_READY",
        "consumer_entrypoint": "manifest.json",
        "index": {
            "manifest_sources": 41,
            "csv_rows": 41,
        },
        "base_verification": {
            "legacy_files": 38,
            "legacy_records": 5407,
            "special_acquisition": 1,
            "external_blockers": 2,
        },
        "duplicates": {
            "exact_duplicate_groups": 5,
            "shared_physical_groups": 5,
            "cross_physical_groups": 0,
        },
        "checksums": {
            "covered_files": 44,
            "mismatches": 0,
        },
        "zip": {
            "zip_entries": 45,
            "content_mismatches": 0,
        },
        "zip_sha256": "abc",
        "package_bytes": 1,
        "zip_bytes": 1,
    }


def test_expected_state_closes_b12():
    report = evaluate(
        enterprise_report=_enterprise_report(),
        fresh_audit=_fresh(),
    )

    assert report["closure_status"] == "CLOSED"
    assert report["failed_checks"] == []


def test_cross_physical_duplicate_keeps_b12_open():
    fresh = _fresh()
    fresh["duplicates"]["shared_physical_groups"] = 4
    fresh["duplicates"]["cross_physical_groups"] = 1

    report = evaluate(
        enterprise_report=_enterprise_report(),
        fresh_audit=fresh,
    )

    assert report["closure_status"] == "OPEN"
    assert "duplicate_groups" in report["failed_checks"]


def test_checksum_and_zip_relation_is_explicit():
    fresh = _fresh()
    fresh["zip"]["zip_entries"] = 44

    report = evaluate(
        enterprise_report=_enterprise_report(),
        fresh_audit=fresh,
    )

    assert report["closure_status"] == "OPEN"
    assert (
        "checksum_zip_count_relation"
        in report["failed_checks"]
    )


def test_consumer_entrypoint_must_remain_manifest():
    fresh = _fresh()
    fresh["consumer_entrypoint"] = "sources.csv"

    report = evaluate(
        enterprise_report=_enterprise_report(),
        fresh_audit=fresh,
    )

    assert report["closure_status"] == "OPEN"
    assert "consumer_contract" in report["failed_checks"]
