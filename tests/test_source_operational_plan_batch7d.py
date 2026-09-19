from __future__ import annotations

from apps.source_operational_plan.main import (
    build_custom_candidates,
    classify_b6_ready,
    merge_source,
)


def test_b6_html_remains_operational_html():
    status, workflow, discover_apis = classify_b6_ready(
        {
            "b6_status": "READY_HTTP_HTML",
            "workflow_strategy": "html",
        }
    )
    assert status == "OPERATIONAL_HTTP_HTML"
    assert workflow == "html"
    assert discover_apis is False


def test_b7_promoted_source_becomes_javascript():
    source = {
        "source_id": "x",
        "b6_status": "B7_B8_CANDIDATE",
        "b6_route": "B7_BROWSER_CHARACTERIZATION",
    }
    browser = {
        "resolution_status": "READY_JAVASCRIPT_FILES",
        "route": "PROMOTE_JAVASCRIPT_WORKFLOW",
        "browser_status": "BROWSER_FILE_DISCOVERY",
        "direct_downloads": 2,
        "decision_note": "Browser encontró archivos.",
    }
    result = merge_source(source, browser)
    assert result["operational_status"] == "OPERATIONAL_JAVASCRIPT"
    assert result["workflow_strategy"] == "javascript"
    assert result["next_phase"] == "OPERATIONAL_CONFIG"


def test_b7_ambiguous_network_goes_to_b8_not_javascript():
    source = {
        "source_id": "x",
        "b6_status": "B7_B8_CANDIDATE",
        "b6_route": "B7_BROWSER_CHARACTERIZATION",
    }
    browser = {
        "resolution_status": "JAVASCRIPT_NETWORK_REVIEW",
        "route": "B7_NETWORK_SEMANTIC_REVIEW",
        "browser_status": "BROWSER_GENERIC_NETWORK_EVIDENCE",
        "direct_downloads": 0,
        "decision_note": "Tráfico no concluyente.",
    }
    result = merge_source(source, browser)
    assert result["operational_status"] == "B8_CUSTOM_CANDIDATE"
    assert result["workflow_strategy"] is None
    assert result["next_phase"] == "B8_CUSTOM"


def test_api_semantic_review_goes_to_custom():
    source = {
        "source_id": "api-x",
        "b6_status": "API_SEMANTIC_REVIEW",
        "b6_route": "REVIEW_BEFORE_PROMOTION",
    }
    result = merge_source(source, None)
    assert result["operational_status"] == "B8_CUSTOM_CANDIDATE"
    assert result["next_phase"] == "B8_CUSTOM"


def test_access_restricted_is_not_sent_to_custom_or_browser():
    source = {
        "source_id": "restricted",
        "b6_status": "ACCESS_RESTRICTED",
        "b6_route": "ACCESS_POLICY_REVIEW",
    }
    result = merge_source(source, None)
    assert result["operational_status"] == "ACCESS_RESTRICTED"
    assert result["browser_required"] is False
    assert result["custom_required"] is False


def test_custom_candidate_builder_only_keeps_b8():
    plan = {
        "sources": [
            {
                "source_id": "a",
                "next_phase": "B8_CUSTOM",
                "operational_status": "B8_CUSTOM_CANDIDATE",
            },
            {
                "source_id": "b",
                "next_phase": "OPERATIONAL_CONFIG",
                "operational_status": "OPERATIONAL_HTTP_HTML",
            },
        ]
    }
    custom = build_custom_candidates(plan)
    assert custom["candidate_count"] == 1
    assert custom["sources"][0]["source_id"] == "a"
