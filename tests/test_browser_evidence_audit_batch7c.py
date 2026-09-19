from __future__ import annotations

from apps.browser_evidence_audit.main import (
    file_kind,
    network_category,
    resolution_for_source,
)


def test_file_kind_structured():
    assert file_kind("https://example.org/data.xlsx") == "STRUCTURED_FILE"


def test_analytics_is_not_data():
    event = {
        "url": "https://www.google-analytics.com/g/collect?v=2",
        "content_type": "application/json",
    }
    assert (
        network_category(event, "https://example.org")
        == "ANALYTICS_TELEMETRY"
    )


def test_same_origin_json_is_review_not_auto_data_api():
    event = {
        "url": "https://example.org/backend/state",
        "content_type": "application/json",
    }
    assert (
        network_category(event, "https://example.org")
        == "SAME_ORIGIN_JSON"
    )


def test_dataset_endpoint_is_data_endpoint():
    event = {
        "url": "https://example.org/api/datasets/42",
        "content_type": "application/json",
    }
    assert (
        network_category(event, "https://example.org")
        == "DATA_ENDPOINT"
    )


def test_direct_files_promote_javascript():
    result = {
        "entrypoint": "https://example.org",
        "direct_download_urls": [
            "https://example.org/files/report.pdf",
        ],
    }
    resolution = resolution_for_source(result, [])
    assert resolution["resolution_status"] == "READY_JAVASCRIPT_FILES"
    assert resolution["workflow_strategy"] == "javascript"


def test_data_endpoint_promotes_javascript():
    result = {
        "entrypoint": "https://example.org",
        "direct_download_urls": [],
    }
    events = [
        {
            "url": "https://example.org/api/datasets/42",
            "content_type": "application/json",
        }
    ]
    resolution = resolution_for_source(result, events)
    assert (
        resolution["resolution_status"]
        == "READY_JAVASCRIPT_DATA_NETWORK"
    )
    assert resolution["workflow_strategy"] == "javascript"


def test_telemetry_only_routes_to_custom():
    result = {
        "entrypoint": "https://example.org",
        "direct_download_urls": [],
    }
    events = [
        {
            "url": "https://www.google-analytics.com/g/collect",
            "content_type": "application/json",
        }
    ]
    resolution = resolution_for_source(result, events)
    assert resolution["resolution_status"] == "B8_CUSTOM_CANDIDATE"
    assert resolution["route"] == "B8_CUSTOM_REVIEW"
