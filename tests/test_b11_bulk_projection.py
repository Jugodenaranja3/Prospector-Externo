from __future__ import annotations

from pathlib import Path

from apps.b11_bulk_projection.main import (
    candidate_rows,
    choose_grouping_contract,
    legacy_records,
    resolve_projection_source_id,
)


def test_candidate_rows_selects_only_raw_ready():
    audit = {
        "sources": [
            {
                "logical_source_id": "b",
                "classification": "SUCCESS_EMPTY",
            },
            {
                "logical_source_id": "a",
                "classification": "RAW_READY_NO_PROJECTION",
            },
            {
                "logical_source_id": "c",
                "classification": "RAW_READY_NO_PROJECTION",
            },
        ]
    }

    selected = candidate_rows(audit)

    assert [row["logical_source_id"] for row in selected] == ["a", "c"]


def test_projection_source_prefers_physical_id(tmp_path):
    row = {
        "logical_source_id": "asfi",
        "physical_source_id": "asfi_valores",
    }

    assert (
        resolve_projection_source_id(row, tmp_path)
        == "asfi_valores"
    )


def test_projection_source_can_read_single_state_source(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "sources.json").write_text(
        '{"physical_shared": {"source_id": "physical_shared"}}',
        encoding="utf-8",
    )

    row = {
        "logical_source_id": "logical",
        "physical_source_id": None,
    }

    assert (
        resolve_projection_source_id(row, tmp_path)
        == "physical_shared"
    )


def test_choose_grouping_contract_prefers_logical(tmp_path):
    grouping = tmp_path / "config" / "grouping"
    grouping.mkdir(parents=True)

    logical = grouping / "aps_soat.yaml"
    physical = grouping / "aps.yaml"

    logical.write_text("x: 1\n", encoding="utf-8")
    physical.write_text("x: 2\n", encoding="utf-8")

    assert (
        choose_grouping_contract(
            tmp_path,
            "aps_soat",
            "aps",
        )
        == logical
    )


def test_legacy_records_counts_only_complete_contract():
    document = {
        "ESTADISTICAS": {
            "A": {
                "ok.csv": {
                    "descripcion": "A",
                    "url_descarga": "https://x/a.xlsx",
                    "fecha_actualizacion": "2026",
                    "tipo_archivo": "XLSX",
                    "url_origen": "https://x/",
                    "metodo_deteccion": "html_link",
                },
                "bad.csv": {
                    "descripcion": "B",
                    "url_descarga": "https://x/b.xlsx",
                },
            }
        }
    }

    assert legacy_records(document) == 1
