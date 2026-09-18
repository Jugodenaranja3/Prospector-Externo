from copy import deepcopy
from typing import Optional

import pytest

from prospector_externo.application.projection_service import DataxProjectionService
from prospector_externo.domain.models import ApiMetadata, ResourceCandidate, ResourceType, Snapshot, Source
from prospector_externo.domain.projection import (
    ProjectionBuilder,
    ProjectionPeriodNormalizer,
    ProjectionPriority,
    ResourceFamilyKeyBuilder,
    ResourceProjectionPolicy,
)


def _file(
    key: str,
    url: str,
    *,
    title: str = "",
    ext: str = "",
    period: Optional[str] = None,
    context: Optional[str] = None,
    origin: str = "https://example.test/stats",
) -> ResourceCandidate:
    return ResourceCandidate(
        resource_key=key,
        url=url,
        raw_url=url,
        source_id="src",
        title=title,
        file_extension=ext,
        period_label=period,
        context_text=context,
        discovered_from_url=origin,
        discovery_method="html_link",
    )


def _api(key: str = "api-1") -> ResourceCandidate:
    return ResourceCandidate(
        resource_key=key,
        url="https://example.test/api/series?page=1&limit=10",
        raw_url="https://example.test/api/series?page=1&limit=10",
        source_id="src",
        title="Serie estadística API",
        resource_type=ResourceType.API,
        discovery_method="api_response",
        content_hash="a" * 64,
        api=ApiMetadata(
            identity="GET https://example.test/api/series",
            format="json",
            method="GET",
            has_pagination=True,
            records_detected=10,
            callable_by_policy=True,
        ),
    )


def test_period_normalizer_keeps_existing_label():
    resource = _file("k", "https://example.test/a.pdf", period="2026-05")
    assert ProjectionPeriodNormalizer.extract(resource) == "2026-05"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Boletín Mayo 2026.pdf", "2026-05"),
        ("reporte_03_2025.xlsx", "2025-03"),
        ("serie-2024-11.csv", "2024-11"),
        ("Indicadores T2 2026", "2026-Q2"),
        ("Anuario 2023", "2023"),
    ],
)
def test_period_normalizer_common_periods(text, expected):
    resource = _file("k", f"https://example.test/{text}", title=text)
    assert ProjectionPeriodNormalizer.extract(resource) == expected


def test_structured_file_is_high_priority_and_selected():
    resource = _file("k", "https://example.test/data_2026_05.xlsx", ext=".xlsx")
    selected, priority, reasons = ResourceProjectionPolicy.classify(resource)
    assert selected is True
    assert priority == ProjectionPriority.HIGH
    assert "structured_data_format" in reasons


def test_public_get_api_is_high_priority_and_selected():
    selected, priority, reasons = ResourceProjectionPolicy.classify(_api())
    assert selected is True
    assert priority == ProjectionPriority.HIGH
    assert reasons == ("public_get_api",)


def test_authenticated_or_non_callable_api_is_not_projected():
    resource = _api()
    resource.api.auth_required = True
    resource.api.callable_by_policy = False
    selected, priority, reasons = ResourceProjectionPolicy.classify(resource)
    assert selected is False
    assert priority == ProjectionPriority.LOW
    assert "api_not_publicly_callable" in reasons


def test_periodic_statistical_pdf_is_conditionally_selected():
    resource = _file(
        "k",
        "https://example.test/boletin_mayo_2026.pdf",
        title="Boletín Estadístico Mayo 2026",
        ext=".pdf",
        period="2026-05",
    )
    selected, priority, reasons = ResourceProjectionPolicy.classify(resource)
    assert selected is True
    assert priority == ProjectionPriority.MEDIUM
    assert "periodic_statistical_document" in reasons


def test_administrative_pdf_stays_out_of_projection_even_with_year():
    resource = _file(
        "k",
        "https://example.test/convocatoria_2026.pdf",
        title="Convocatoria pública 2026",
        ext=".pdf",
        period="2026",
    )
    selected, priority, reasons = ResourceProjectionPolicy.classify(resource)
    assert selected is False
    assert priority == ProjectionPriority.LOW
    assert reasons == ("administrative_document",)


def test_unknown_archive_without_data_evidence_is_not_assumed_to_contain_data():
    resource = _file("k", "https://example.test/adjuntos.zip", title="Adjuntos", ext=".zip")
    selected, priority, reasons = ResourceProjectionPolicy.classify(resource)
    assert selected is False
    assert priority == ProjectionPriority.LOW
    assert reasons == ("archive_contents_unknown",)


def test_family_key_groups_same_series_across_periods_and_formats():
    a = _file(
        "a",
        "https://example.test/financiera_01_2026.xlsx",
        title="financiera_01_2026.xlsx",
        ext=".xlsx",
    )
    b = _file(
        "b",
        "https://example.test/financiera_02_2026.ods",
        title="financiera_02_2026.ods",
        ext=".ods",
    )
    assert ResourceFamilyKeyBuilder.build(a) == "financiera"
    assert ResourceFamilyKeyBuilder.build(b) == "financiera"


def test_context_can_recover_family_when_anchor_is_only_download():
    resource = _file(
        "k",
        "https://example.test/reporte-financiero-05-2026.pdf",
        title="Descargar",
        ext=".pdf",
        context="Reporte Financiero Mensual Mayo 2026 Descargar",
    )
    assert ResourceFamilyKeyBuilder.build(resource) == "reporte-financiero-mensual"


def test_projection_groups_representations_by_family_and_period():
    resources = [
        _file("xlsx", "https://e.test/serie_05_2026.xlsx", title="Serie 05 2026.xlsx", ext=".xlsx", period="2026-05"),
        _file("ods", "https://e.test/serie_05_2026.ods", title="Serie 05 2026.ods", ext=".ods", period="2026-05"),
        _file("pdf", "https://e.test/serie_05_2026.pdf", title="Serie estadística mayo 2026.pdf", ext=".pdf", period="2026-05"),
    ]
    projection = ProjectionBuilder.build(
        source_id="src",
        source_name="SRC",
        entrypoint="https://e.test",
        run_id="run-1",
        resources_hash="hash",
        resources=resources,
    )
    assert projection.total_raw_resources == 3
    assert projection.total_selected_resources == 3
    assert projection.total_families == 1
    family = projection.families[0]
    assert family.family_key == "serie"
    assert family.latest_period == "2026-05"
    assert family.available_formats == ["xlsx", "ods", "pdf"]
    assert len(family.periods) == 1
    period = family.periods[0]
    assert [r.resource_key for r in period.representations] == ["xlsx", "ods", "pdf"]
    assert period.preferred_resource_key == "xlsx"


def test_projection_orders_periods_latest_first():
    resources = [
        _file("old", "https://e.test/serie_2025_12.csv", title="Serie 2025-12", ext=".csv", period="2025-12"),
        _file("new", "https://e.test/serie_2026_01.csv", title="Serie 2026-01", ext=".csv", period="2026-01"),
        _file("year", "https://e.test/serie_2024.csv", title="Serie 2024", ext=".csv", period="2024"),
    ]
    projection = ProjectionBuilder.build(
        source_id="src",
        source_name="SRC",
        entrypoint="https://e.test",
        run_id="run-1",
        resources_hash="hash",
        resources=resources,
    )
    assert [p.period_label for p in projection.families[0].periods] == ["2026-01", "2025-12", "2024"]


def test_projection_is_deterministic_independent_of_input_order():
    resources = [
        _file("b", "https://e.test/b_2026.csv", title="B 2026", ext=".csv", period="2026"),
        _file("a", "https://e.test/a_2026.xlsx", title="A 2026", ext=".xlsx", period="2026"),
        _api("api-z"),
    ]
    kwargs = dict(
        source_id="src",
        source_name="SRC",
        entrypoint="https://e.test",
        run_id="run-1",
        resources_hash="hash",
    )
    first = ProjectionBuilder.build(resources=resources, **kwargs)
    second = ProjectionBuilder.build(resources=list(reversed(resources)), **kwargs)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_projection_does_not_mutate_raw_resource_candidates():
    resource = _file(
        "k",
        "https://e.test/reporte_05_2026.pdf",
        title="Reporte estadístico mayo 2026",
        ext=".pdf",
    )
    before = deepcopy(resource.model_dump(mode="json"))
    ProjectionBuilder.build(
        source_id="src",
        source_name="SRC",
        entrypoint="https://e.test",
        run_id="run-1",
        resources_hash="hash",
        resources=[resource],
    )
    assert resource.model_dump(mode="json") == before


def test_low_priority_decision_is_auditable_but_not_in_families():
    resource = _file(
        "admin",
        "https://e.test/manual.pdf",
        title="Manual institucional",
        ext=".pdf",
    )
    projection = ProjectionBuilder.build(
        source_id="src",
        source_name="SRC",
        entrypoint="https://e.test",
        run_id="run-1",
        resources_hash="hash",
        resources=[resource],
    )
    assert projection.total_raw_resources == 1
    assert projection.total_selected_resources == 0
    assert projection.total_families == 0
    assert projection.decisions[0].resource_key == "admin"
    assert projection.decisions[0].selected is False


def test_application_service_preserves_source_and_snapshot_traceability():
    source = Source(
        source_id="src",
        name="Fuente Real",
        entrypoint="https://example.test",
        workflow="html",
    )
    snapshot = Snapshot(
        source_id="src",
        run_id="run-123",
        resources_hash="abc123",
        total_resources=1,
        resources=[_file("k", "https://example.test/data.csv", title="Datos", ext=".csv")],
    )
    projection = DataxProjectionService().project(source, snapshot)
    assert projection.source_id == "src"
    assert projection.source_name == "Fuente Real"
    assert projection.entrypoint == "https://example.test"
    assert projection.run_id == "run-123"
    assert projection.resources_hash == "abc123"


def test_application_service_rejects_cross_source_snapshot():
    source = Source(source_id="a", name="A", entrypoint="https://a.test", workflow="html")
    snapshot = Snapshot(source_id="b", run_id="run", resources_hash="h", total_resources=0)
    with pytest.raises(ValueError):
        DataxProjectionService().project(source, snapshot)


def test_projection_contract_does_not_invent_analize_file_report_or_database_fields():
    projection = ProjectionBuilder.build(
        source_id="src",
        source_name="SRC",
        entrypoint="https://e.test",
        run_id="run-1",
        resources_hash="hash",
        resources=[_file("k", "https://e.test/data.csv", title="Datos", ext=".csv")],
    )
    dumped = projection.model_dump(mode="json")
    forbidden = {
        "download_type", "report_code", "storage_table", "conversion_factor",
        "decimal_separator", "data_base", "database", "migration_rule",
    }

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key.lower() not in forbidden
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(dumped)


def test_api_family_key_uses_identity_before_generic_source_title():
    first = _api("api-1")
    second = _api("api-2")
    first.title = "Fuente API"
    second.title = "Fuente API"
    second.url = "https://example.test/api/other?page=1"
    second.api.identity = "GET https://example.test/api/other"
    assert ResourceFamilyKeyBuilder.build(first) != ResourceFamilyKeyBuilder.build(second)


def test_api_projection_does_not_collapse_distinct_endpoints_with_same_title():
    first = _api("api-1")
    second = _api("api-2")
    first.title = "Fuente API"
    second.title = "Fuente API"
    second.url = "https://example.test/api/other?page=1"
    second.api.identity = "GET https://example.test/api/other"
    projection = ProjectionBuilder.build(
        source_id="src",
        source_name="Fuente API",
        entrypoint="https://example.test",
        run_id="run-api",
        resources_hash="hash-api",
        resources=[first, second],
    )
    assert projection.total_selected_resources == 2
    assert projection.total_families == 2


def test_openapi_spec_document_is_not_confused_with_dataset():
    resource = _api("openapi-spec")
    resource.api.is_openapi = True
    resource.api.format = "openapi"
    resource.discovery_method = "api_documentation_reference"
    selected, priority, reasons = ResourceProjectionPolicy.classify(resource)
    assert selected is False
    assert priority == ProjectionPriority.LOW
    assert reasons == ("api_documentation_not_dataset",)


def test_public_get_operation_discovered_from_openapi_is_projected():
    resource = _api("openapi-operation")
    resource.api.is_openapi = True
    resource.api.operation_id = "listSeries"
    resource.api.records_detected = None
    resource.discovery_method = "openapi_get_operation"
    selected, priority, reasons = ResourceProjectionPolicy.classify(resource)
    assert selected is True
    assert priority == ProjectionPriority.HIGH
    assert reasons == ("public_get_api",)
