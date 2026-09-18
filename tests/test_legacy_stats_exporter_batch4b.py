import json
from copy import deepcopy
from pathlib import Path

import pytest

from prospector_externo.application.legacy_stats_exporter import (
    LEGACY_REQUIRED_FIELDS,
    LegacyExportPlan,
    LegacyStatsContractValidator,
    LegacyStatsJsonExporter,
)
from prospector_externo.domain.models import ApiMetadata, ResourceCandidate, ResourceType
from prospector_externo.domain.projection import ProjectionBuilder


FIXTURES = Path(__file__).parent / "fixtures"


def _resource(
    key: str,
    url: str,
    *,
    title: str,
    ext: str,
    period: str,
    origin: str,
    method: str = "url_pattern",
    raw_url: str | None = None,
    context: str | None = None,
):
    return ResourceCandidate(
        resource_key=key,
        source_id="aps",
        resource_type=ResourceType.FILE,
        url=url,
        raw_url=raw_url or url,
        title=title,
        file_extension=ext,
        period_label=period,
        discovered_from_url=origin,
        discovery_method=method,
        context_text=context,
    )


def _projection(resources):
    return ProjectionBuilder.build(
        source_id="aps",
        source_name="APS",
        entrypoint="https://example.test",
        run_id="run-4b",
        resources_hash="hash-4b",
        resources=resources,
    )


def _golden_projection_and_plan():
    resources = [
        _resource(
            "mayo",
            "https://example.test/boletin_mensual_pensiones_05_2026.pdf?token=abc",
            raw_url="https://example.test/boletin_mensual_pensiones_05_2026.pdf?token=abc",
            title="Boletín Mensual Pensiones Mayo 2026.pdf",
            ext=".pdf",
            period="2026-05",
            origin="https://example.test/estadisticas/2026",
        ),
        _resource(
            "marzo",
            "https://example.test/boletin_mensual_pensiones_03_2026.xlsx",
            title="Boletín Mensual Pensiones Marzo 2026.xlsx",
            ext=".xlsx",
            period="2026-03",
            origin="https://example.test/estadisticas/2026",
            method="html_link",
        ),
        _resource(
            "t1",
            "https://example.test/boletin_trimestral_pensiones_t1_2026.pdf",
            title="Boletín Trimestral de Pensiones T1 2026.pdf",
            ext=".pdf",
            period="2026-Q1",
            origin="https://example.test/trimestral/2026",
        ),
    ]
    projection = _projection(resources)
    monthly = next(f.family_key for f in projection.families if "mensual" in f.family_key)
    quarterly = next(f.family_key for f in projection.families if "trimestral" in f.family_key)
    plan = LegacyExportPlan(
        family_routes={
            monthly: ("{year}",),
            quarterly: ("files", "Boletin_Trimestral_Estadistico_de_Pensiones", "{year}"),
        }
    )
    return projection, plan


def test_historical_fixture_matches_observed_legacy_contract_and_has_24_records():
    document = json.loads((FIXTURES / "legacy_stats_historical_contract.json").read_text(encoding="utf-8"))
    assert LegacyStatsContractValidator.validate(document) == 24


def test_exporter_matches_exact_representative_golden_fixture():
    projection, plan = _golden_projection_and_plan()
    actual = LegacyStatsJsonExporter.build(projection, plan)
    expected = json.loads((FIXTURES / "legacy_stats_export_golden.json").read_text(encoding="utf-8"))
    assert actual.document == expected
    assert actual.record_count == 3


def test_legacy_slot_keeps_csv_suffix_even_when_actual_file_is_pdf_or_xlsx():
    projection, plan = _golden_projection_and_plan()
    root = LegacyStatsJsonExporter.build(projection, plan).document["ESTADISTICAS"]["2026"]
    assert set(root) == {"Descargar.csv", "Descargar_2.csv"}
    assert root["Descargar.csv"]["tipo_archivo"] == "PDF"
    assert root["Descargar_2.csv"]["tipo_archivo"] == "XLSX"


def test_every_record_has_exact_six_historical_fields():
    projection, plan = _golden_projection_and_plan()
    document = LegacyStatsJsonExporter.build(projection, plan).document
    assert LegacyStatsContractValidator.validate(document) == 3
    records = []

    def walk(node):
        if isinstance(node, dict):
            if set(node) == set(LEGACY_REQUIRED_FIELDS):
                records.append(node)
            else:
                for value in node.values():
                    walk(value)

    walk(document)
    assert len(records) == 3
    assert all(set(record) == set(LEGACY_REQUIRED_FIELDS) for record in records)


def test_exporter_preserves_all_representations_not_only_preferred_one():
    resources = [
        _resource("xlsx", "https://e.test/serie_05_2026.xlsx", title="Serie Mayo 2026.xlsx", ext=".xlsx", period="2026-05", origin="https://e.test/o"),
        _resource("ods", "https://e.test/serie_05_2026.ods", title="Serie Mayo 2026.ods", ext=".ods", period="2026-05", origin="https://e.test/o"),
        _resource("pdf", "https://e.test/serie_05_2026.pdf", title="Serie estadística Mayo 2026.pdf", ext=".pdf", period="2026-05", origin="https://e.test/o"),
    ]
    projection = _projection(resources)
    result = LegacyStatsJsonExporter.build(projection)
    assert result.record_count == 3


def test_raw_projection_is_not_mutated_by_legacy_export():
    projection, plan = _golden_projection_and_plan()
    before = deepcopy(projection.model_dump(mode="json"))
    LegacyStatsJsonExporter.build(projection, plan)
    assert projection.model_dump(mode="json") == before


def test_export_is_deterministic_even_if_family_and_representation_order_changes():
    projection, plan = _golden_projection_and_plan()
    first = LegacyStatsJsonExporter.build(projection, plan).document

    reversed_projection = projection.model_copy(deep=True)
    reversed_projection.families.reverse()
    for family in reversed_projection.families:
        family.periods.reverse()
        for period in family.periods:
            period.representations.reverse()
    second = LegacyStatsJsonExporter.build(reversed_projection, plan).document
    assert first == second


def test_raw_url_is_preferred_for_legacy_download_url():
    resource = _resource(
        "raw",
        "https://e.test/data.pdf?a=1&b=2",
        raw_url="https://e.test/data.pdf?b=2&a=1&token=keep",
        title="Datos estadísticos 2026.pdf",
        ext=".pdf",
        period="2026",
        origin="https://e.test/origin",
    )
    projection = _projection([resource])
    doc = LegacyStatsJsonExporter.build(projection).document
    leaf = doc["ESTADISTICAS"]["files"]
    record = next(iter(next(iter(leaf.values())).values()))["Descargar.csv"]
    assert record["url_descarga"] == "https://e.test/data.pdf?b=2&a=1&token=keep"


def test_default_route_is_source_agnostic_and_uses_family_and_year():
    resource = _resource(
        "k",
        "https://e.test/serie_2026.csv",
        title="Serie 2026.csv",
        ext=".csv",
        period="2026",
        origin="https://e.test/o",
    )
    projection = _projection([resource])
    family = projection.families[0]
    doc = LegacyStatsJsonExporter.build(projection).document
    assert "files" in doc["ESTADISTICAS"]
    family_segment = LegacyStatsJsonExporter._legacy_segment(family.title)
    assert "2026" in doc["ESTADISTICAS"]["files"][family_segment]


def test_custom_route_can_reproduce_historical_nonperiodic_branch():
    resource = _resource(
        "funeral",
        "https://e.test/gastos_funerares_mayo_2022.pdf",
        title="ESTADISTICAS DE GASTOS FUNERALES AL 31 DE MAYO DE 2022.pdf",
        ext=".pdf",
        period="2022-05",
        origin="https://e.test/renta",
    )
    projection = _projection([resource])
    family_key = projection.families[0].family_key
    plan = LegacyExportPlan(
        family_routes={family_key: ("OTROS", "DOCUMENTOS_GENERALES", "VARIOS")}
    )
    doc = LegacyStatsJsonExporter.build(projection, plan).document
    assert doc["ESTADISTICAS"]["OTROS"]["DOCUMENTOS_GENERALES"]["VARIOS"]["Descargar.csv"]["fecha_actualizacion"] == "2022-05"


def test_no_period_uses_explicit_sentinel_in_default_route():
    resource = _resource(
        "np",
        "https://e.test/datos.csv",
        title="Datos abiertos",
        ext=".csv",
        period="",
        origin="https://e.test/o",
    )
    resource.period_label = None
    projection = _projection([resource])
    doc = LegacyStatsJsonExporter.build(projection).document
    family = projection.families[0]
    family_segment = LegacyStatsJsonExporter._legacy_segment(family.title)
    assert "SIN_PERIODO" in doc["ESTADISTICAS"]["files"][family_segment]


def test_atomic_writer_produces_valid_json_and_leaves_no_temp_file(tmp_path):
    projection, plan = _golden_projection_and_plan()
    destination = tmp_path / "estadisticas.json"
    result = LegacyStatsJsonExporter.write_atomic(destination, projection, plan)
    assert result.record_count == 3
    loaded = json.loads(destination.read_text(encoding="utf-8"))
    assert loaded == result.document
    assert list(tmp_path.glob(".estadisticas.json.*.tmp")) == []


def test_atomic_writer_replaces_existing_file(tmp_path):
    projection, plan = _golden_projection_and_plan()
    destination = tmp_path / "estadisticas.json"
    destination.write_text('{"old": true}', encoding="utf-8")
    LegacyStatsJsonExporter.write_atomic(destination, projection, plan)
    loaded = json.loads(destination.read_text(encoding="utf-8"))
    assert "ESTADISTICAS" in loaded and "old" not in loaded


def test_validator_rejects_extra_fields_that_would_break_exact_contract():
    bad = {
        "ESTADISTICAS": {
            "2026": {
                "Descargar.csv": {
                    "descripcion": "x",
                    "url_descarga": "https://e.test/x.pdf",
                    "fecha_actualizacion": "2026",
                    "tipo_archivo": "PDF",
                    "url_origen": "https://e.test",
                    "metodo_deteccion": "html_link",
                    "resource_key": "should-not-leak",
                }
            }
        }
    }
    with pytest.raises(ValueError):
        LegacyStatsContractValidator.validate(bad)


def test_export_never_leaks_projection_or_analize_internal_fields():
    projection, plan = _golden_projection_and_plan()
    dumped = json.dumps(LegacyStatsJsonExporter.build(projection, plan).document, ensure_ascii=False)
    forbidden = [
        "resource_key", "priority", "reason_codes", "preferred_resource_key",
        "report_code", "storage_table", "conversion_factor", "decimal_separator",
        "data_base", "migration_rule",
    ]
    assert all(name not in dumped for name in forbidden)


def test_custom_root_key_is_supported_but_default_remains_estadisticas():
    projection, _ = _golden_projection_and_plan()
    default = LegacyStatsJsonExporter.build(projection).document
    assert set(default) == {"ESTADISTICAS"}
    custom = LegacyStatsJsonExporter.build(projection, LegacyExportPlan(root_key="ROOT")).document
    assert set(custom) == {"ROOT"}


def test_public_api_projection_exports_using_observed_structured_format():
    api = ResourceCandidate(
        resource_key="api",
        source_id="aps",
        resource_type=ResourceType.API,
        url="https://e.test/api/series?page=1",
        raw_url="https://e.test/api/series?page=1",
        title="Series API",
        discovered_from_url="https://e.test/docs",
        discovery_method="api_endpoint",
        api=ApiMetadata(
            identity="GET https://e.test/api/series",
            format="json",
            method="GET",
            callable_by_policy=True,
            auth_required=False,
        ),
    )
    projection = _projection([api])
    result = LegacyStatsJsonExporter.build(projection)
    assert result.record_count == 1
    dumped = json.dumps(result.document, ensure_ascii=False)
    assert '"tipo_archivo": "JSON"' in dumped
    assert '"url_descarga": "https://e.test/api/series?page=1"' in dumped
