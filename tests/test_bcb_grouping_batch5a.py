from __future__ import annotations

from pathlib import Path

import yaml

from prospector_externo.domain.grouping import GroupingContract, GroupingContractResolver
from prospector_externo.domain.models import DiscoveryType, ResourceCandidate, SourceConfig
from prospector_externo.domain.projection import (
    ProjectionBuilder,
    ProjectionPeriodNormalizer,
    ResourceFamilyKeyBuilder,
)
from prospector_externo.workflows.html_workflow import HtmlWorkflow


ROOT = Path(__file__).resolve().parents[1]


def load_contract() -> GroupingContract:
    payload = yaml.safe_load((ROOT / "config/grouping/bcb.yaml").read_text(encoding="utf-8"))
    return GroupingContract.model_validate(payload)


def resource(
    key: str,
    url: str,
    *,
    origin: str,
    title: str,
    context: str,
    period: str | None = None,
) -> ResourceCandidate:
    return ResourceCandidate(
        resource_key=key,
        url=url,
        raw_url=url,
        source_id="bcb",
        title=title,
        anchor_text=title,
        context_text=context,
        discovered_from_url=origin,
        file_extension="." + url.split(".")[-1].split("?")[0].lower(),
        period_label=period,
        discovery_method="html_link",
    )


def test_bcb_source_config_is_declarative_and_conservative():
    data = yaml.safe_load((ROOT / "config/sources.yaml").read_text(encoding="utf-8"))
    raw = next(item for item in data["sources"] if item["source_id"] == "bcb")
    config = SourceConfig.model_validate(raw)
    assert config.workflow == "html"
    assert config.grouping_contract == "config/grouping/bcb.yaml"
    assert config.ignore_robots_txt is False
    assert config.rate_limit_seconds >= 1.0
    assert config.max_depth == 1
    assert config.max_requests <= 120
    assert config.max_urls <= 120
    assert set(config.allowed_hosts) == {"bcb.gob.bo", "www.bcb.gob.bo"}
    assert any("reporte-estadistico" in seed for seed in config.seeds)
    assert any("pub_boletin-mensual" in seed for seed in config.seeds)


def test_period_normalizer_supports_weekly_daily_granularity():
    r = resource(
        "weekly",
        "https://www.bcb.gob.bo/webdocs/05_estadisticassemanales/Semanal%2038_2026.xlsx",
        origin="https://www.bcb.gob.bo/?q=estad-sticas-semanales",
        title="Ver archivo Excel",
        context="AL 11 DE SEPTIEMBRE DE 2026 | Información Estadística Semanal",
    )
    assert ProjectionPeriodNormalizer.extract(r) == "2026-09-11"


def test_html_section_context_uses_statistical_period_not_publication_folder():
    html = """
    <html><body>
      <h2>Índice · Estadísticas (Diciembre 2025)</h2>
      <h3>Sector Monetario 1–24</h3>
      <ul><li><a href="/webdocs/publicacionesbcb/2026/01/31/01.xlsx">1. Base Monetaria</a></li></ul>
    </body></html>
    """
    config = SourceConfig(
        source_id="bcb",
        name="BCB",
        entrypoint="https://www.bcb.gob.bo",
        seeds=[],
        allowed_hosts=["bcb.gob.bo", "www.bcb.gob.bo"],
    )
    workflow = HtmlWorkflow()
    resources, _ = workflow._extract_resources_and_links(
        html,
        "https://www.bcb.gob.bo/?q=pub_boletin-mensual",
        config,
        discovery_type=DiscoveryType.HTML,
    )
    assert len(resources) == 1
    assert resources[0].period_label == "2025-12"
    assert "Diciembre 2025" in (resources[0].context_text or "")


def test_html_section_context_recovers_weekly_date():
    html = """
    <html><body>
      <div>AL 11 DE SEPTIEMBRE DE 2026</div>
      <div>Información Estadística Semanal <a href="/webdocs/05_estadisticassemanales/Semanal%2038_2026.xlsx">Ver archivo Excel</a></div>
    </body></html>
    """
    config = SourceConfig(
        source_id="bcb",
        name="BCB",
        entrypoint="https://www.bcb.gob.bo",
        seeds=[],
        allowed_hosts=["bcb.gob.bo", "www.bcb.gob.bo"],
    )
    workflow = HtmlWorkflow()
    resources, _ = workflow._extract_resources_and_links(
        html,
        "https://www.bcb.gob.bo/?q=estad-sticas-semanales",
        config,
        discovery_type=DiscoveryType.HTML,
    )
    assert len(resources) == 1
    assert resources[0].period_label == "2026-09-11"


def test_report_statistico_pdf_xlsx_ods_share_family_and_period():
    contract = load_contract()
    origin = "https://www.bcb.gob.bo/?q=reporte-estadistico"
    context = "OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - JULIO 2026"
    reps = [
        resource(
            "xlsx",
            "https://www.bcb.gob.bo/webdocs/sistema_pagos/Reporte%20Estad%C3%ADstico%20a%20Julio%202026.xlsx",
            origin=origin,
            title="Ver archivo Excel",
            context=context,
            period="2026-07",
        ),
        resource(
            "ods",
            "https://www.bcb.gob.bo/webdocs/sistema_pagos/Reporte%20Estad%C3%ADstico%20a%20Julio%202026.ods",
            origin=origin,
            title="Ver archivo Ods",
            context=context,
            period="2026-07",
        ),
        resource(
            "pdf",
            "https://www.bcb.gob.bo/webdocs/sistema_pagos/Reporte%20Estad%C3%ADstico%20a%20Julio%202026.pdf",
            origin=origin,
            title="Ver archivo Pdf",
            context=context,
            period="2026-07",
        ),
    ]
    projection = ProjectionBuilder.build(
        source_id="bcb",
        source_name="Banco Central de Bolivia",
        entrypoint="https://www.bcb.gob.bo",
        run_id="fixture",
        resources_hash="fixture",
        resources=reps,
        grouping_contract=contract,
    )
    assert projection.total_families == 1
    family = projection.families[0]
    assert family.family_key == "sistema-pagos-reporte-estadistico"
    assert family.latest_period == "2026-07"
    assert family.available_formats == ["xlsx", "ods", "pdf"]
    assert len(family.periods[0].representations) == 3
    assert family.periods[0].preferred_resource_key == "xlsx"
    assert {d.grouping_rule_id for d in projection.decisions} == {"bcb_sistema_pagos_reporte_estadistico"}


def test_bcb_filename_copy_suffix_does_not_split_system_payments_family():
    contract = load_contract()
    r = resource(
        "june-xlsx",
        "https://www.bcb.gob.bo/webdocs/sistema_pagos/Reporte%20Estad%C3%ADstico%20a%20Junio%202026%20%281%29.xlsx",
        origin="https://www.bcb.gob.bo/?q=reporte-estadistico",
        title="Ver archivo Excel",
        context="OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - JUNIO 2026",
        period="2026-06",
    )
    resolved = GroupingContractResolver.resolve(contract, r)
    assert resolved is not None
    assert resolved.family_key == "sistema-pagos-reporte-estadistico"


def test_monthly_table_code_groups_across_publication_dates_but_not_other_tables():
    contract = load_contract()
    origin = "https://www.bcb.gob.bo/?q=pub_boletin-mensual"
    dec = resource(
        "dec-01",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2026/01/31/01.xlsx",
        origin=origin,
        title="1. Base Monetaria",
        context="Índice · Estadísticas (Diciembre 2025) | Sector Monetario 1–24",
        period="2025-12",
    )
    nov = resource(
        "nov-01",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2025/12/24/01.xlsx",
        origin=origin,
        title="1. Base Monetaria",
        context="Índice · Estadísticas (Noviembre 2025) | Sector Monetario 1–24",
        period="2025-11",
    )
    exports = resource(
        "dec-25",
        "https://www.bcb.gob.bo/webdocs/publicacionesbcb/2026/01/31/25.xlsx",
        origin=origin,
        title="25. Exportaciones",
        context="Índice · Estadísticas (Diciembre 2025) | Sector Externo",
        period="2025-12",
    )
    projection = ProjectionBuilder.build(
        source_id="bcb",
        source_name="Banco Central de Bolivia",
        entrypoint="https://www.bcb.gob.bo",
        run_id="fixture",
        resources_hash="fixture",
        resources=[dec, nov, exports],
        grouping_contract=contract,
    )
    assert projection.total_families == 2
    by_key = {f.family_key: f for f in projection.families}
    base = next(f for k, f in by_key.items() if k.startswith("boletin-mensual-cuadro-1-base-monetaria"))
    assert [p.period_label for p in base.periods] == ["2025-12", "2025-11"]
    assert any(k.startswith("boletin-mensual-cuadro-25-exportaciones") for k in by_key)


def test_weekly_contract_produces_one_series_with_daily_periods():
    contract = load_contract()
    origin = "https://www.bcb.gob.bo/?q=estad-sticas-semanales"
    r1 = resource(
        "w38",
        "https://www.bcb.gob.bo/webdocs/05_estadisticassemanales/Semanal%2038_2026.xlsx",
        origin=origin,
        title="Ver archivo Excel",
        context="AL 11 DE SEPTIEMBRE DE 2026 | Información Estadística Semanal",
    )
    r2 = resource(
        "w37",
        "https://www.bcb.gob.bo/webdocs/05_estadisticassemanales/Semanal%2037_2026.xlsx",
        origin=origin,
        title="Ver archivo Excel",
        context="AL 04 DE SEPTIEMBRE DE 2026 | Información Estadística Semanal",
    )
    projection = ProjectionBuilder.build(
        source_id="bcb",
        source_name="Banco Central de Bolivia",
        entrypoint="https://www.bcb.gob.bo",
        run_id="fixture",
        resources_hash="fixture",
        resources=[r1, r2],
        grouping_contract=contract,
    )
    assert projection.total_families == 1
    family = projection.families[0]
    assert family.family_key == "informacion-estadistica-semanal"
    assert family.latest_period == "2026-09-11"
    assert [p.period_label for p in family.periods] == ["2026-09-11", "2026-09-04"]


def test_generic_family_builder_treats_excel_link_text_as_generic():
    r = resource(
        "excel-link",
        "https://www.bcb.gob.bo/webdocs/sistema_pagos/Reporte%20Estad%C3%ADstico%20a%20Julio%202026.xlsx",
        origin="https://www.bcb.gob.bo/?q=reporte-estadistico",
        title="Ver archivo Excel",
        context="OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - JULIO 2026",
        period="2026-07",
    )
    assert ResourceFamilyKeyBuilder.build(r) == "operaciones-del-sistema-de-pagos-nacional"


def test_grouping_contract_rejects_other_source():
    contract = load_contract()
    r = resource(
        "x",
        "https://www.bcb.gob.bo/a.xlsx",
        origin="https://www.bcb.gob.bo/?q=reporte-estadistico",
        title="Ver archivo Excel",
        context="OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - JULIO 2026",
    )
    r.source_id = "asfi-bcb"
    try:
        GroupingContractResolver.resolve(contract, r)
    except ValueError as exc:
        assert "no aplica" in str(exc)
    else:
        raise AssertionError("Un grouping contract no debe cruzar source_id")


def test_offline_pipeline_applies_grouping_contract(tmp_path):
    from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
    from prospector_externo.application.projection_export_pipeline import DataxProjectionExportPipeline
    from prospector_externo.domain.models import Snapshot, Source

    contract = load_contract()
    origin = "https://www.bcb.gob.bo/?q=reporte-estadistico"
    context = "OPERACIONES DEL SISTEMA DE PAGOS NACIONAL - JULIO 2026"
    reps = [
        resource(
            "xlsx-pipe",
            "https://www.bcb.gob.bo/webdocs/sistema_pagos/Reporte%20Estad%C3%ADstico%20a%20Julio%202026.xlsx",
            origin=origin,
            title="Ver archivo Excel",
            context=context,
            period="2026-07",
        ),
        resource(
            "ods-pipe",
            "https://www.bcb.gob.bo/webdocs/sistema_pagos/Reporte%20Estad%C3%ADstico%20a%20Julio%202026.ods",
            origin=origin,
            title="Ver archivo Ods",
            context=context,
            period="2026-07",
        ),
    ]
    repo = LocalJsonRepositoryAdapter(tmp_path)
    repo.save_source(
        Source(
            source_id="bcb",
            name="Banco Central de Bolivia (BCB)",
            entrypoint="https://www.bcb.gob.bo",
            workflow="html",
        )
    )
    repo.save_snapshot(
        Snapshot(
            source_id="bcb",
            run_id="fixture-bcb-5a",
            resources_hash="fixture-hash",
            total_resources=len(reps),
            resources=reps,
        )
    )
    manifest = DataxProjectionExportPipeline(repo).export(
        "bcb",
        tmp_path,
        grouping_contract=contract,
    )
    assert manifest.families == 1
    import json

    projection = json.loads((tmp_path / manifest.projection_relpath).read_text(encoding="utf-8"))
    assert projection["families"][0]["family_key"] == "sistema-pagos-reporte-estadistico"
    assert projection["families"][0]["periods"][0]["preferred_resource_key"] == "xlsx-pipe"
