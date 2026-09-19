from __future__ import annotations

import json
from pathlib import Path

from apps.b11_output_audit.main import (
    classify_source,
    legacy_records,
    resource_statistics,
)


def test_resource_statistics_counts_types_priority_and_extensions():
    snapshot = {
        "total_resources": 4,
        "resources": [
            {
                "url": "https://x.test/a.xlsx",
                "resource_type": "file",
                "file_extension": ".xlsx",
            },
            {
                "url": "https://x.test/b.pdf",
                "resource_type": "file",
                "file_extension": ".pdf",
            },
            {
                "url": "https://x.test/api",
                "resource_type": "api",
                "api": {"format": "json"},
            },
            {
                "url": "https://x.test/unknown",
                "resource_type": "file",
            },
        ],
    }

    stats = resource_statistics(snapshot)

    assert stats["snapshot_total"] == 4
    assert stats["resources_len"] == 4
    assert stats["resource_types"] == {"api": 1, "file": 3}
    assert stats["extensions"] == {".pdf": 1, ".xlsx": 1}
    assert stats["api_formats"] == {"json": 1}
    assert stats["high_priority"] == 2
    assert stats["medium_priority"] == 1


def test_legacy_records_requires_complete_historical_contract():
    document = {
        "ESTADISTICAS": {
            "A": {
                "ok.csv": {
                    "descripcion": "OK",
                    "url_descarga": "https://x.test/a.xlsx",
                    "fecha_actualizacion": "2026-09",
                    "tipo_archivo": "XLSX",
                    "url_origen": "https://x.test/",
                    "metodo_deteccion": "html_link",
                },
                "bad.csv": {
                    "descripcion": "BAD",
                    "url_descarga": "https://x.test/b.xlsx",
                },
            }
        }
    }

    count, invalid = legacy_records(document)

    assert count == 2
    assert invalid == 1


def test_classification_prefers_external_blocker_policy():
    assert (
        classify_source(
            source_id="mhe",
            report_present=True,
            execution_status="FAILED",
            raw_resource_count=0,
            legacy_record_count=0,
        )
        == "EXTERNAL_BLOCKER"
    )


def test_classification_distinguishes_raw_projection_and_empty():
    assert (
        classify_source(
            source_id="bcb",
            report_present=True,
            execution_status="SUCCESS",
            raw_resource_count=10,
            legacy_record_count=0,
        )
        == "RAW_READY_NO_PROJECTION"
    )

    assert (
        classify_source(
            source_id="bcb",
            report_present=True,
            execution_status="SUCCESS",
            raw_resource_count=10,
            legacy_record_count=8,
        )
        == "DATAX_READY"
    )

    assert (
        classify_source(
            source_id="bcb",
            report_present=True,
            execution_status="SUCCESS",
            raw_resource_count=0,
            legacy_record_count=0,
        )
        == "SUCCESS_EMPTY"
    )


def test_classification_flags_missing_evidence():
    assert (
        classify_source(
            source_id="bcb",
            report_present=False,
            execution_status=None,
            raw_resource_count=None,
            legacy_record_count=0,
        )
        == "EVIDENCE_MISSING"
    )
