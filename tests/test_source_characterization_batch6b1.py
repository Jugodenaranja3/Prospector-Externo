from __future__ import annotations

import json
from pathlib import Path

from apps.source_characterization.main import classify_stage, inspect_resource_evidence


def test_api_resources_are_deduplicated_by_url(tmp_path: Path):
    resource = {
        "url": "https://example.org/api/data",
        "raw_url": "https://example.org/api/data",
        "discovery_method": "api_endpoint",
        "resource_type": "api",
        "api": {"endpoint": "/api/data"},
    }
    for index in range(3):
        (tmp_path / f"artifact_{index}.json").write_text(
            json.dumps({"datasets": [{"resources": [resource]}]}),
            encoding="utf-8",
        )

    evidence = inspect_resource_evidence(tmp_path)
    assert evidence["resource_urls"] == ["https://example.org/api/data"]
    assert evidence["api_resources"] == 1
    assert evidence["api_resource_urls"] == ["https://example.org/api/data"]
    assert evidence["resource_methods"]["api_endpoint"] == 1
    assert evidence["resource_types"]["api"] == 1


def test_failed_redirect_chain_ending_403_is_access_restricted():
    trace = [
        {"method": "GET", "url": "https://example.org/", "status_code": 307, "is_robots": False, "host": "example.org"},
        {"method": "GET", "url": "https://example.org/home", "status_code": 308, "is_robots": False, "host": "example.org"},
        {"method": "GET", "url": "https://example.org/final", "status_code": 403, "is_robots": False, "host": "example.org"},
    ]
    status, reason = classify_stage(
        returncode=1,
        execution_status="FAILED",
        resources_found=0,
        trace=trace,
        log_text="403 Forbidden",
        timed_out=False,
    )
    assert status == "ACCESS_RESTRICTED"
    assert reason == "SITE_HTTP_403"


def test_resources_win_over_incidental_403():
    trace = [
        {"method": "GET", "url": "https://example.org/", "status_code": 200, "is_robots": False, "host": "example.org"},
        {"method": "GET", "url": "https://example.org/private", "status_code": 403, "is_robots": False, "host": "example.org"},
    ]
    status, _ = classify_stage(
        returncode=0,
        execution_status="SUCCESS",
        resources_found=5,
        trace=trace,
        log_text="",
        timed_out=False,
    )
    assert status == "SUCCESS_RESOURCES"


def test_successful_site_with_internal_403_and_no_resources_remains_reachable():
    trace = [
        {"method": "GET", "url": "https://example.org/", "status_code": 200, "is_robots": False, "host": "example.org"},
        {"method": "GET", "url": "https://example.org/private", "status_code": 403, "is_robots": False, "host": "example.org"},
    ]
    status, _ = classify_stage(
        returncode=0,
        execution_status="SUCCESS",
        resources_found=0,
        trace=trace,
        log_text="",
        timed_out=False,
    )
    assert status == "REACHABLE_NO_RESOURCES"
