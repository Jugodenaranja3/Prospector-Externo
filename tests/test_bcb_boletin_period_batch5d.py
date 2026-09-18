from __future__ import annotations

from pathlib import Path

import yaml

from prospector_externo.domain.grouping import GroupingContract, GroupingContractResolver
from prospector_externo.domain.models import DiscoveryType, SourceConfig
from prospector_externo.workflows.html_workflow import HtmlWorkflow

ROOT = Path(__file__).resolve().parents[1]


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="bcb",
        name="BCB test",
        entrypoint="https://www.bcb.gob.bo/?q=pub_boletin-mensual",
        workflow="html",
        seeds=["https://www.bcb.gob.bo/?q=pub_boletin-mensual"],
    )


def _resource(title: str, context: str, url: str):
    return HtmlWorkflow()._make_resource(
        config=_config(),
        raw_url=url,
        current_url="https://www.bcb.gob.bo/?q=pub_boletin-mensual",
        title=title,
        discovery_type=DiscoveryType.HTML,
        anchor_text=title,
        context_text=context,
    )


def _contract() -> GroupingContract:
    payload = yaml.safe_load(
        (ROOT / "config/grouping/bcb.yaml").read_text(encoding="utf-8")
    )
    return GroupingContract.model_validate(payload)


def test_bcb_boletin_context_month_beats_base_year_in_title():
    resource = _resource(
        "43. Índice de Precios al Consumidor - IPC (Base 2016 = 100)",
        "Índice · Boletín Mensual N° 373 (Enero 2026)",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2026/02/27/43.xlsx",
    )
    assert resource.period_label == "2026-01"


def test_explicit_title_month_keeps_priority_when_context_has_another_month():
    resource = _resource(
        "Reporte Estadístico Julio 2024",
        "Índice · Boletín Mensual N° 373 (Enero 2026)",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2026/02/27/example.xlsx",
    )
    assert resource.period_label == "2024-07"


def test_period_evidence_is_not_cross_mixed_between_fields():
    workflow = HtmlWorkflow()
    assert workflow._extract_period_from_text(
        "43. IPC Base 2016 = 100 Índice · Boletín Mensual Enero 2026"
    ) == "2016-01"
    assert workflow._extract_period_from_evidence(
        "43. IPC Base 2016 = 100",
        "Índice · Boletín Mensual Enero 2026",
    ) == "2026-01"


def test_bcb_boletin_rule_keeps_3a_and_3b_as_distinct_series():
    contract = _contract()
    a = _resource(
        "3A. Balance del Banco Central de Bolivia (Activo)",
        "Índice · Boletín Mensual N° 373 (Enero 2026)",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2026/02/27/03A.xlsx",
    )
    b = _resource(
        "3B. Balance del Banco Central de Bolivia (Pasivo)",
        "Índice · Boletín Mensual N° 373 (Enero 2026)",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2026/02/27/03B.xlsx",
    )
    ra = GroupingContractResolver.resolve(contract, a)
    rb = GroupingContractResolver.resolve(contract, b)
    assert ra is not None and rb is not None
    assert ra.family_key != rb.family_key
    assert a.period_label == b.period_label == "2026-01"
