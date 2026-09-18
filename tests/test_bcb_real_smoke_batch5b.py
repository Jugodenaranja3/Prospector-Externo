from __future__ import annotations

import json
from pathlib import Path

import yaml

from prospector_externo.domain.grouping import GroupingContract, GroupingUnmatchedPolicy
from prospector_externo.domain.models import ResourceCandidate, Snapshot, Source
from prospector_externo.domain.projection import ProjectionBuilder


ROOT = Path(__file__).resolve().parents[1]


def _contract() -> GroupingContract:
    payload = yaml.safe_load((ROOT / "config/grouping/bcb.yaml").read_text(encoding="utf-8"))
    return GroupingContract.model_validate(payload)


def _real_snapshot() -> Snapshot:
    return Snapshot.model_validate_json(
        (ROOT / "tests/fixtures/bcb_real_snapshot_batch5b.json").read_text(encoding="utf-8")
    )


def _real_source() -> Source:
    return Source.model_validate_json(
        (ROOT / "tests/fixtures/bcb_real_source_batch5b.json").read_text(encoding="utf-8")
    )


def test_bcb_contract_is_explicit_deny_by_default_downstream():
    contract = _contract()
    assert contract.schema_version == "grouping-contract-1.1"
    assert contract.unmatched_policy == GroupingUnmatchedPolicy.EXCLUDE


def test_grouping_contract_default_preserves_backward_compatible_fallback():
    contract = GroupingContract(source_id="demo", rules=[])
    assert contract.unmatched_policy == GroupingUnmatchedPolicy.FALLBACK
    resource = ResourceCandidate(
        resource_key="xlsx",
        url="https://example.test/data.xlsx",
        source_id="demo",
        title="Datos",
        file_extension=".xlsx",
        discovery_method="html_link",
    )
    projection = ProjectionBuilder.build(
        source_id="demo",
        source_name="Demo",
        entrypoint="https://example.test",
        run_id="run",
        resources_hash="hash",
        resources=[resource],
        grouping_contract=contract,
    )
    assert projection.total_selected_resources == 1
    assert projection.decisions[0].selected is True


def test_unmatched_structured_resource_is_preserved_raw_but_excluded_from_projection():
    contract = GroupingContract(
        source_id="demo",
        unmatched_policy="exclude",
        rules=[],
    )
    resource = ResourceCandidate(
        resource_key="xlsx",
        url="https://example.test/data.xlsx",
        source_id="demo",
        title="Dataset XLSX",
        file_extension=".xlsx",
        discovery_method="html_link",
    )
    projection = ProjectionBuilder.build(
        source_id="demo",
        source_name="Demo",
        entrypoint="https://example.test",
        run_id="run",
        resources_hash="hash",
        resources=[resource],
        grouping_contract=contract,
    )
    assert projection.total_raw_resources == 1
    assert projection.total_selected_resources == 0
    decision = projection.decisions[0]
    assert decision.selected is False
    assert decision.priority.value == "LOW"
    assert "structured_data_format" in decision.reason_codes
    assert "grouping_contract_unmatched" in decision.reason_codes


def test_real_bcb_smoke_raw_catalog_remains_39_resources():
    snapshot = _real_snapshot()
    assert snapshot.total_resources == 39
    assert len(snapshot.resources) == 39
    assert sum(1 for r in snapshot.resources if r.period_label == "2026-07") == 3


def test_real_bcb_smoke_projects_only_characterized_system_payments_series():
    snapshot = _real_snapshot()
    source = _real_source()
    projection = ProjectionBuilder.build(
        source_id=source.source_id,
        source_name=source.name,
        entrypoint=source.entrypoint,
        run_id=snapshot.run_id,
        resources_hash=snapshot.resources_hash,
        resources=snapshot.resources,
        grouping_contract=_contract(),
    )

    assert projection.total_raw_resources == 39
    assert projection.total_selected_resources == 27
    assert projection.total_families == 1

    family = projection.families[0]
    assert family.family_key == "sistema-pagos-reporte-estadistico"
    assert family.title == "Reporte Estadístico de Operaciones del Sistema de Pagos Nacional"
    assert family.latest_period == "2026-07"
    assert family.available_formats == ["xlsx", "ods", "pdf"]
    assert [p.period_label for p in family.periods] == [
        "2026-07", "2026-06", "2026-05", "2026-04", "2026-03",
        "2026-02", "2026-01", "2025-12", "2025-11",
    ]
    assert all(len(p.representations) == 3 for p in family.periods)
    assert all(
        {r.format for r in p.representations} == {"xlsx", "ods", "pdf"}
        for p in family.periods
    )


def test_real_bcb_smoke_has_exactly_12_unmatched_resources_audited_not_deleted():
    snapshot = _real_snapshot()
    source = _real_source()
    projection = ProjectionBuilder.build(
        source_id=source.source_id,
        source_name=source.name,
        entrypoint=source.entrypoint,
        run_id=snapshot.run_id,
        resources_hash=snapshot.resources_hash,
        resources=snapshot.resources,
        grouping_contract=_contract(),
    )

    unmatched = [d for d in projection.decisions if "grouping_contract_unmatched" in d.reason_codes]
    assert len(unmatched) == 12
    assert all(d.selected is False for d in unmatched)
    assert all(d.priority.value == "LOW" for d in unmatched)
    # El XLSX global de Reservas Internacionales fue el caso más peligroso del smoke:
    # sigue en raw, pero ya no entra al downstream solo por ser XLSX.
    raw_by_key = {r.resource_key: r for r in snapshot.resources}
    assert any(
        "Reservas_Internacionales_Netas" in raw_by_key[d.resource_key].url
        for d in unmatched
    )


def test_real_bcb_smoke_matched_decisions_use_the_explicit_grouping_rule():
    snapshot = _real_snapshot()
    source = _real_source()
    projection = ProjectionBuilder.build(
        source_id=source.source_id,
        source_name=source.name,
        entrypoint=source.entrypoint,
        run_id=snapshot.run_id,
        resources_hash=snapshot.resources_hash,
        resources=snapshot.resources,
        grouping_contract=_contract(),
    )
    selected = [d for d in projection.decisions if d.selected]
    assert len(selected) == 27
    assert {d.grouping_rule_id for d in selected} == {"bcb_sistema_pagos_reporte_estadistico"}
    assert {d.family_key for d in selected} == {"sistema-pagos-reporte-estadistico"}
