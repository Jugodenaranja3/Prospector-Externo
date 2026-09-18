from __future__ import annotations

from apps.source_resolution.main import (
    derive_ready_decision,
    derive_reconciliation_decision,
)


def test_cms_api_candidate_is_not_promoted_to_api_workflow():
    source = {
        "characterization_status": "API_CANDIDATE",
        "workflow_hint": "api",
        "max_resources_observed": 17,
        "max_api_resources_observed": 12,
    }
    audit = {
        "api_quality": "CMS_OR_METADATA_ONLY",
        "file_quality": "LINK_OR_API_EVIDENCE",
        "resource_count": 17,
        "api_resource_count": 12,
    }
    result = derive_ready_decision(source, audit)
    assert result["b6_status"] == "READY_HTML_WITH_API_DISCOVERY"
    assert result["workflow_strategy"] == "html"
    assert result["discover_apis"] is True


def test_real_data_api_is_promoted_to_api():
    source = {
        "characterization_status": "API_CANDIDATE",
        "workflow_hint": "api",
    }
    audit = {
        "api_quality": "DATA_API_EVIDENCE",
        "resource_count": 10,
        "api_resource_count": 5,
    }
    result = derive_ready_decision(source, audit)
    assert result["b6_status"] == "READY_DATA_API"
    assert result["workflow_strategy"] == "api"


def test_reconciled_reachable_without_resources_goes_to_b7_b8():
    source = {"entrypoint": "https://old.example.org"}
    recon = {
        "action": "RETRY_CRAWL",
        "lifecycle": "CURRENT_DOMAIN_CHANGED",
        "candidate_entrypoint": "https://example.org",
    }
    run = {
        "status": "REACHABLE_NO_RESOURCES",
        "resources_found": 0,
    }
    result = derive_reconciliation_decision(source, recon, run)
    assert result["b6_status"] == "B7_B8_CANDIDATE"
    assert result["browser_review"] is True


def test_retired_source_is_not_substituted():
    source = {"entrypoint": "https://legacy.example.org"}
    recon = {
        "action": "RETIRED_NO_SUBSTITUTION",
        "lifecycle": "RETIRED_SUCCESSOR_IDENTIFIED",
        "candidate_entrypoint": None,
    }
    result = derive_reconciliation_decision(source, recon, None)
    assert result["b6_status"] == "RETIRED_HISTORICAL_SOURCE"
    assert result["workflow_strategy"] is None
    assert result["browser_review"] is False


def test_access_restricted_does_not_request_bypass():
    source = {"entrypoint": "https://restricted.example.org"}
    recon = {
        "action": "HOLD_ACCESS_REVIEW",
        "lifecycle": "CURRENT_ACCESS_RESTRICTED",
        "candidate_entrypoint": "https://restricted.example.org",
    }
    result = derive_reconciliation_decision(source, recon, None)
    assert result["b6_status"] == "ACCESS_RESTRICTED"
    assert result["b6_route"] == "ACCESS_POLICY_REVIEW"
    assert result["browser_review"] is False
