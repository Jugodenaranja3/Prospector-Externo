from __future__ import annotations

import json
from pathlib import Path

from apps.source_evidence_audit.main import (
    classify_api_url,
    classify_failure,
    extract_unique_resources,
)


def test_wordpress_api_is_not_treated_as_data_api():
    assert (
        classify_api_url("https://example.org/wp-json/wp/v2/posts")
        == "CMS_WORDPRESS_API"
    )


def test_data_api_signal_is_detected():
    assert (
        classify_api_url("https://example.org/api/datasets/123")
        == "POSSIBLE_DATA_API"
    )


def test_unique_resources_are_deduped_across_artifacts(tmp_path: Path):
    resource = {
        "url": "https://example.org/file.xlsx",
        "discovery_method": "html_link",
        "resource_type": "file",
    }
    for idx in range(3):
        (tmp_path / f"{idx}.json").write_text(
            json.dumps({"datasets": [{"resources": [resource]}]}),
            encoding="utf-8",
        )
    rows = extract_unique_resources(tmp_path)
    assert len(rows) == 1
    assert rows[0]["extension"] == ".xlsx"


def test_no_http_trace_is_explicit():
    row = {
        "characterization_status": "EXECUTION_ERROR",
        "stages": [
            {
                "site_http_codes": [],
                "robots_http_codes": [],
            }
        ],
    }
    reason, route = classify_failure(row, "")
    assert reason == "NO_HTTP_TRACE_EXECUTION_FAILURE"
    assert route == "SOURCE_URL_OR_NETWORK_REVIEW"


def test_robots_5xx_without_site_is_explicit():
    row = {
        "characterization_status": "EXECUTION_ERROR",
        "stages": [
            {
                "site_http_codes": [],
                "robots_http_codes": [500],
            }
        ],
    }
    reason, route = classify_failure(row, "")
    assert reason == "ROBOTS_HTTP_5XX_BLOCKING"
    assert route == "ROBOTS_OR_SOURCE_REVIEW"
