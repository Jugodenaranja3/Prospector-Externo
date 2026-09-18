from __future__ import annotations

from pathlib import Path

from apps.source_characterization.main import (
    build_stage_config,
    final_characterization,
    first_stage_for_baseline,
    group_inventory_by_entrypoint,
)


def test_green_starts_with_expanded_html():
    assert first_stage_for_baseline("GREEN_RESOURCES") == "expanded_html"


def test_reachable_starts_with_expanded_html():
    assert first_stage_for_baseline("REACHABLE_NO_RESOURCES") == "expanded_html"


def test_execution_error_starts_with_recovery():
    assert first_stage_for_baseline("EXECUTION_ERROR") == "recovery_http"


def test_stage_config_expanded_html_enables_sitemap_but_not_api():
    source = {
        "source_id": "demo",
        "name": "Demo",
        "entrypoint": "https://example.org",
    }
    cfg = build_stage_config(
        source,
        stage="expanded_html",
        max_requests=12,
        max_runtime_seconds=45,
        rate_limit_seconds=1.0,
        max_depth=2,
        max_urls=150,
    )["sources"][0]

    assert cfg["workflow"] == "html"
    assert cfg["max_requests"] == 12
    assert cfg["max_depth"] == 2
    assert cfg["discover_sitemaps"] is True
    assert cfg["discover_apis"] is False
    assert cfg["ignore_robots_txt"] is False


def test_stage_config_api_enriched_enables_api_not_browser():
    source = {
        "source_id": "demo",
        "name": "Demo",
        "entrypoint": "https://example.org",
    }
    cfg = build_stage_config(
        source,
        stage="api_enriched",
        max_requests=15,
        max_runtime_seconds=45,
        rate_limit_seconds=1.0,
        max_depth=1,
        max_urls=150,
    )["sources"][0]

    assert cfg["discover_sitemaps"] is False
    assert cfg["discover_apis"] is True
    assert cfg["follow_api_pagination"] is True
    assert cfg["probe_api_documentation"] is True
    assert cfg["ignore_robots_txt"] is False


def test_final_characterization_prefers_api_when_api_evidence_exists():
    stages = [
        {
            "stage": "api_enriched",
            "status": "SUCCESS_RESOURCES",
            "resources_found": 4,
            "api_resources": 4,
            "resource_methods": {"api_endpoint": 4},
        }
    ]
    result = final_characterization(stages)
    assert result["status"] == "API_CANDIDATE"
    assert result["suggested_workflow"] == "api"


def test_final_characterization_html_when_normal_files_found():
    stages = [
        {
            "stage": "expanded_html",
            "status": "SUCCESS_RESOURCES",
            "resources_found": 8,
            "api_resources": 0,
            "resource_methods": {"html_link": 8},
        }
    ]
    result = final_characterization(stages)
    assert result["status"] == "HTTP_HTML_CANDIDATE"
    assert result["suggested_workflow"] == "html"


def test_no_resources_does_not_auto_assign_javascript():
    stages = [
        {
            "stage": "expanded_html",
            "status": "REACHABLE_NO_RESOURCES",
            "resources_found": 0,
            "api_resources": 0,
            "resource_methods": {},
        },
        {
            "stage": "api_enriched",
            "status": "REACHABLE_NO_RESOURCES",
            "resources_found": 0,
            "api_resources": 0,
            "resource_methods": {},
        },
    ]
    result = final_characterization(stages)
    assert result["status"] == "REACHABLE_NEEDS_DEEPER_REVIEW"
    assert result["suggested_workflow"] is None
    assert result["recommended_next_phase"] == "B7_OR_CUSTOM_REVIEW"


def test_group_inventory_keeps_logical_sources_separate():
    inventory = [
        {
            "source_id": "aps",
            "logical_code": "APS",
            "entrypoint": "https://www.aps.gob.bo",
        },
        {
            "source_id": "aps_soat",
            "logical_code": "APS/SOAT",
            "entrypoint": "https://www.aps.gob.bo",
        },
    ]
    grouped = group_inventory_by_entrypoint(inventory)
    assert len(grouped) == 1
    assert len(next(iter(grouped.values()))) == 2
