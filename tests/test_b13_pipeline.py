from __future__ import annotations

import json
from pathlib import Path

from apps.b13_pipeline.main import (
    exact_duplicate_groups,
    legacy_records,
    package_ready,
)


def _record():
    return {
        "descripcion": "Dataset",
        "url_descarga": "https://example.test/a.csv",
        "fecha_actualizacion": "2026-09",
        "tipo_archivo": "CSV",
        "url_origen": "https://example.test/",
        "metodo_deteccion": "html_link",
    }


def test_legacy_records_accepts_historical_contract():
    document = {
        "ESTADISTICAS": {
            "CAT": {
                "RAMA": {
                    "a.csv": _record(),
                }
            }
        }
    }

    assert legacy_records(document) == 1


def test_package_ready_accepts_terminal_operational_states():
    rows = []

    for index in range(38):
        rows.append(
            {
                "logical_source_id": f"ready_{index}",
                "classification": "DATAX_READY",
            }
        )

    rows.extend(
        [
            {
                "logical_source_id": "transtats",
                "classification": "ACQUISITION_JOB_READY",
            },
            {
                "logical_source_id": "mhe",
                "classification": "EXTERNAL_BLOCKER",
            },
            {
                "logical_source_id": "sigma",
                "classification": "EXTERNAL_BLOCKER",
            },
        ]
    )

    assert package_ready(rows) is True


def test_package_ready_rejects_partial_run():
    rows = [
        {
            "logical_source_id": f"s{i}",
            "classification": "DATAX_READY",
        }
        for i in range(40)
    ]
    rows.append(
        {
            "logical_source_id": "pending",
            "classification": "PENDING",
        }
    )

    assert package_ready(rows) is False


def test_exact_duplicate_groups_classifies_shared_physical():
    rows = [
        {
            "logical_source_id": "a",
            "physical_source_id": "shared",
            "sha256": "hash",
        },
        {
            "logical_source_id": "b",
            "physical_source_id": "shared",
            "sha256": "hash",
        },
        {
            "logical_source_id": "c",
            "physical_source_id": "c",
            "sha256": "other",
        },
    ]

    groups = exact_duplicate_groups(rows)

    assert len(groups) == 1
    assert groups[0]["logical_source_ids"] == ["a", "b"]
    assert groups[0]["shared_physical_source"] is True


def test_exact_duplicate_groups_flags_cross_physical():
    rows = [
        {
            "logical_source_id": "a",
            "physical_source_id": "pa",
            "sha256": "hash",
        },
        {
            "logical_source_id": "b",
            "physical_source_id": "pb",
            "sha256": "hash",
        },
    ]

    groups = exact_duplicate_groups(rows)

    assert len(groups) == 1
    assert groups[0]["shared_physical_source"] is False


def test_operational_roster_uses_durable_plan_not_b11_runtime(tmp_path):
    import yaml
    from apps.b13_pipeline.main import operational_roster

    config = tmp_path / "config"
    config.mkdir()
    rows = [
        {
            "source_id": f"operational_{index}",
            "next_phase": "OPERATIONAL_CONFIG",
        }
        for index in range(41)
    ]
    rows.extend(
        {
            "source_id": f"status_{index}",
            "next_phase": "B10_STATUS",
        }
        for index in range(11)
    )
    (config / "source_operational_plan.yaml").write_text(
        yaml.safe_dump({"sources": rows}, sort_keys=False),
        encoding="utf-8",
    )

    result = operational_roster(tmp_path)
    assert len(result) == 41
    assert "operational_0" in result
    assert not (tmp_path / ".runtime" / "b11_output_audit" / "latest.json").exists()
