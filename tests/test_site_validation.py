from __future__ import annotations

from src.site_validation.core import ReferenceCandidate, canonical_url, compare_urls


def test_audit_canonicalizer_is_independent_and_removes_tracking():
    value = canonical_url("HTTPS://Example.COM:443/a//b?utm_source=x&z=2&a=1#frag")
    assert value == "https://example.com/a/b?a=1&z=2"


def test_compare_urls_separates_raw_and_legacy_coverage():
    reference = [
        ReferenceCandidate(
            url="https://example.test/a.csv",
            raw_url="https://example.test/a.csv",
            title="A",
            discovered_from="https://example.test/",
            method="html_link",
            extension=".csv",
        ),
        ReferenceCandidate(
            url="https://example.test/b.csv",
            raw_url="https://example.test/b.csv",
            title="B",
            discovered_from="https://example.test/",
            method="html_link",
            extension=".csv",
        ),
    ]
    raw = [
        {"url": "https://example.test/a.csv"},
        {"url": "https://example.test/c.pdf"},
    ]
    legacy = [
        {
            "descripcion": "A",
            "url_descarga": "https://example.test/a.csv",
            "fecha_actualizacion": "",
            "tipo_archivo": "CSV",
            "url_origen": "https://example.test/",
            "metodo_deteccion": "html_link",
        }
    ]
    result = compare_urls(reference, raw, legacy)
    assert result["counts"]["reference_matched_raw"] == 1
    assert result["counts"]["reference_only_raw"] == 1
    assert result["counts"]["raw_only_reference"] == 1
    assert result["counts"]["reference_matched_legacy"] == 1
    assert result["counts"]["reference_only_legacy"] == 1


def test_compare_urls_resolves_relative_legacy_downloads_from_origin():
    reference = [
        ReferenceCandidate(
            url="https://example.test/files/a.xlsx",
            raw_url="https://example.test/files/a.xlsx",
            title="A",
            discovered_from="https://example.test/stats",
            method="html_link",
            extension=".xlsx",
        )
    ]
    raw = [{"url": "https://example.test/files/a.xlsx"}]
    legacy = [
        {
            "descripcion": "A",
            "url_descarga": "/files/a.xlsx",
            "fecha_actualizacion": "2026",
            "tipo_archivo": "XLSX",
            "url_origen": "https://example.test/stats",
            "metodo_deteccion": "html_link",
        }
    ]

    result = compare_urls(reference, raw, legacy, base_url="https://example.test")

    assert result["counts"]["baseline_legacy"] == 1
    assert result["counts"]["reference_matched_legacy"] == 1
    assert result["counts"]["reference_only_legacy"] == 0

def test_compare_urls_resolves_relative_raw_from_discovered_from():
    reference = [
        ReferenceCandidate(
            url="https://example.test/files/a.xlsx",
            raw_url="https://example.test/files/a.xlsx",
            title="A",
            discovered_from="https://example.test/stats",
            method="html_link",
            extension=".xlsx",
        )
    ]
    raw = [
        {
            "raw_url": "/files/a.xlsx",
            "url": "/files/a.xlsx",
            "discovered_from_url": "https://example.test/?q=stats",
        }
    ]
    legacy = []

    result = compare_urls(reference, raw, legacy, base_url="https://example.test")

    assert result["counts"]["baseline_raw_records"] == 1
    assert result["counts"]["baseline_raw"] == 1
    assert result["counts"]["reference_matched_raw"] == 1
    assert result["counts"]["reference_only_raw"] == 0

def test_origin_recheck_comparison_only_scores_successfully_rechecked_origins():
    from src.site_validation.origin_recheck import compare_origin_recheck

    reference = [
        ReferenceCandidate(
            url="https://example.test/files/a.xlsx",
            raw_url="https://example.test/files/a.xlsx",
            title="A",
            discovered_from="https://example.test/stats",
            method="origin_recheck_html_link",
            extension=".xlsx",
        ),
        ReferenceCandidate(
            url="https://example.test/files/new.xlsx",
            raw_url="https://example.test/files/new.xlsx",
            title="New",
            discovered_from="https://example.test/stats",
            method="origin_recheck_html_link",
            extension=".xlsx",
        ),
    ]
    raw = [
        {
            "raw_url": "/files/a.xlsx",
            "discovered_from_url": "https://example.test/stats",
        },
        {
            "raw_url": "/files/old.xlsx",
            "discovered_from_url": "https://example.test/failed-page",
        },
    ]
    result = compare_origin_recheck(
        reference=reference,
        raw=raw,
        legacy=[],
        successful_origins=["https://example.test/stats"],
        base_url="https://example.test",
    )
    counts = result["counts"]
    assert counts["raw_total_unique"] == 2
    assert counts["raw_recheckable_unique"] == 1
    assert counts["reference_matched_raw"] == 1
    assert counts["reference_only_raw"] == 1
    assert counts["raw_recheckable_not_observed"] == 0

def test_a6_operational_batch_uses_physical_groups_and_reserves_special_sources():
    from pathlib import Path

    from src.site_validation.batch import build_physical_targets
    from src.site_validation.core import AuditRoster

    roster = AuditRoster(Path("."))
    ordinary = build_physical_targets(roster, include_special=False)
    all_targets = build_physical_targets(roster, include_special=True)

    assert len(ordinary) == 32
    assert len(all_targets) == 35
    assert {target.physical_source_id for target in all_targets} - {
        target.physical_source_id for target in ordinary
    } == {"mhe", "sigma", "transtats"}

    bcb = next(target for target in ordinary if target.physical_source_id == "bcb")
    assert bcb.representative_source_id == "bcb"
    assert set(bcb.logical_source_ids) == {"bcb", "asfi_bcb"}


def test_a7_classification_separates_html_specialized_and_existing_origin() -> None:
    from src.site_validation.a7 import classify_a7_row

    category, flags, overlap = classify_a7_row(
        operational_status="OPERATIONAL_HTTP_HTML",
        raw_unique=100,
        reference_candidates=90,
        matched_raw=90,
        stop_reason="QUEUE_EXHAUSTED",
        has_origin_evidence=False,
    )
    assert category == "GENERIC_STRONG"
    assert overlap == 0.9

    category, flags, overlap = classify_a7_row(
        operational_status="OPERATIONAL_HTTP_HTML",
        raw_unique=100,
        reference_candidates=50,
        matched_raw=20,
        stop_reason="REQUEST_BUDGET_REACHED",
        has_origin_evidence=False,
    )
    assert category == "ORIGIN_RECHECK_REQUIRED"
    assert "LOW_SITEWIDE_OVERLAP" in flags
    assert "BASELINE_STOP_REQUEST_BUDGET_REACHED" in flags

    category, _, _ = classify_a7_row(
        operational_status="OPERATIONAL_DATA_API",
        raw_unique=10,
        reference_candidates=0,
        matched_raw=0,
        stop_reason="API_SEEDS_EXHAUSTED",
        has_origin_evidence=False,
    )
    assert category == "SPECIALIZED_VALIDATION"

    category, _, _ = classify_a7_row(
        operational_status="OPERATIONAL_HTTP_HTML",
        raw_unique=100,
        reference_candidates=10,
        matched_raw=10,
        stop_reason="QUEUE_EXHAUSTED",
        has_origin_evidence=True,
    )
    assert category == "ORIGIN_EVIDENCE_AVAILABLE"


def test_specialized_targets_are_exactly_six() -> None:
    from src.site_validation.specialized import SPECIALIZED_TARGETS

    assert set(SPECIALIZED_TARGETS) == {
        "data_gov",
        "fifa",
        "sicoes",
        "statistics_denmark",
        "undata",
        "vipfe",
    }

def test_a8_status_only_conservative_classification() -> None:
    from src.site_validation.status_only import classify_status_only_result

    classification, reasons = classify_status_only_result(
        prior_status="NO_PUBLIC_DATA_EVIDENCE",
        pages_visited=10,
        pages_failed=0,
        machine_readable=0,
        documents=20,
        archives=0,
        semantic_hits=1,
        stop_reason="QUEUE_EXHAUSTED",
    )
    assert classification == "STATUS_ONLY_SUPPORTED"
    assert "PUBLIC_DOCUMENTS_FOUND_BUT_NOT_ENOUGH_FOR_PROMOTION" in reasons

    classification, reasons = classify_status_only_result(
        prior_status="NO_PUBLIC_DATA_EVIDENCE",
        pages_visited=10,
        pages_failed=0,
        machine_readable=2,
        documents=0,
        archives=0,
        semantic_hits=2,
        stop_reason="QUEUE_EXHAUSTED",
    )
    assert classification == "REVIEW_FOR_PROMOTION"
    assert "MACHINE_READABLE_PUBLIC_RESOURCES_FOUND" in reasons



def test_a9_special_cases_are_exactly_three() -> None:
    from src.site_validation.a9 import SPECIAL_CASES, classify_external_blocker

    assert SPECIAL_CASES == ("mhe", "sigma", "transtats")
    assert classify_external_blocker(
        dns_ok=True, homepage_ok=False, homepage_error_type="SSLError",
        robots_status=None, robots_error_type="SSLError"
    ) == "EXTERNAL_TLS_BLOCKER_CONFIRMED"
    assert classify_external_blocker(
        dns_ok=True, homepage_ok=True, homepage_error_type=None,
        robots_status=503, robots_error_type=None
    ) == "EXTERNAL_ROBOTS_5XX_CONFIRMED"
    assert classify_external_blocker(
        dns_ok=True, homepage_ok=True, homepage_error_type=None,
        robots_status=200, robots_error_type=None
    ) == "EXTERNAL_BLOCKER_STATE_CHANGED"

def test_a10_prior_delivery_statuses() -> None:
    from src.site_validation.final_matrix import _prior_delivery_status

    assert _prior_delivery_status("mhe", "OPERATIONAL_CONFIG") == "EXTERNAL_BLOCKER"
    assert _prior_delivery_status("sigma", "OPERATIONAL_CONFIG") == "EXTERNAL_BLOCKER"
    assert _prior_delivery_status("transtats", "OPERATIONAL_CONFIG") == "ACQUISITION_JOB_READY"
    assert _prior_delivery_status("bcb", "OPERATIONAL_CONFIG") == "DATAX_READY"
    assert _prior_delivery_status("fmi", "STATUS_ONLY") == "STATUS_ONLY"

def test_a11_effectiveness_summary_requires_52_rows() -> None:
    import pytest
    from src.site_validation.final_report import build_effectiveness_summary

    with pytest.raises(ValueError, match="52"):
        build_effectiveness_summary({"rows": []})
