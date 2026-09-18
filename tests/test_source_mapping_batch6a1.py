from __future__ import annotations

import json
from pathlib import Path

from apps.source_mapping.main import (
    classify_probe,
    parse_request_trace,
    split_http_codes,
    write_reports,
)


def test_request_trace_separates_robots_from_site():
    log = """
2026 INFO httpx: HTTP Request: GET https://example.org/robots.txt "HTTP/1.1 404 Not Found"
2026 INFO httpx: HTTP Request: GET https://example.org "HTTP/1.1 200 OK"
"""
    trace = parse_request_trace(log)
    robots, site = split_http_codes(trace)
    assert robots == [404]
    assert site == [200]


def test_robots_404_plus_site_200_is_not_http_404():
    log = """
HTTP Request: GET https://example.org/robots.txt "HTTP/1.1 404 Not Found"
HTTP Request: GET https://example.org "HTTP/1.1 200 OK"
"""
    trace = parse_request_trace(log)
    status, action = classify_probe(
        returncode=0,
        log_text=log,
        resources_found=0,
        execution_status="SUCCESS",
        timed_out=False,
        request_trace=trace,
    )
    assert status == "REACHABLE_NO_RESOURCES"
    assert action == "review_seed_or_workflow"


def test_robots_403_without_site_request_is_robots_blocked():
    log = 'HTTP Request: GET https://example.org/robots.txt "HTTP/1.1 403 Forbidden"'
    trace = parse_request_trace(log)
    status, action = classify_probe(
        returncode=0,
        log_text=log,
        resources_found=0,
        execution_status="SUCCESS",
        timed_out=False,
        request_trace=trace,
    )
    assert status == "ROBOTS_BLOCKED"
    assert action == "review_robots_policy"


def test_internal_404_does_not_override_green_resources():
    log = """
HTTP Request: GET https://example.org/robots.txt "HTTP/1.1 200 OK"
HTTP Request: GET https://example.org "HTTP/1.1 200 OK"
HTTP Request: GET https://example.org/missing "HTTP/1.1 404 Not Found"
"""
    trace = parse_request_trace(log)
    status, _ = classify_probe(
        returncode=0,
        log_text=log,
        resources_found=4,
        execution_status="SUCCESS",
        timed_out=False,
        request_trace=trace,
    )
    assert status == "GREEN_RESOURCES"


def test_write_reports_creates_logical_manifests(tmp_path: Path):
    row = {
        "source_id": "demo",
        "logical_code": "DEMO",
        "name": "Demo",
        "entrypoint": "https://example.org",
        "host_key": "example.org",
        "status": "GREEN_RESOURCES",
        "coverage_level": "L1_RESOURCE_DISCOVERY",
        "probe_profile": "HTTP_HTML_BASELINE",
        "resources_found": 2,
        "robots_http_codes": [200],
        "site_http_codes": [200],
        "request_trace": [],
        "requests_reported": 2,
        "execution_status": "SUCCESS",
        "stop_reason": "QUEUE_EXHAUSTED",
        "elapsed_seconds": 1.0,
        "next_action": "candidate_for_b6_http_html",
        "error_excerpt": None,
        "reused_probe_from": None,
        "crawl_output_dir": ".runtime/source_mapping/crawls/demo",
        "log_file": ".runtime/source_mapping/logs/demo.log",
        "probe_config_file": ".runtime/source_mapping/probe_configs/demo.yaml",
    }
    write_reports([row], tmp_path, physical_probes=1)

    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "latest.csv").exists()
    assert (tmp_path / "latest.md").exists()
    logical = tmp_path / "logical_sources" / "demo.json"
    assert logical.exists()
    assert json.loads(logical.read_text(encoding="utf-8"))["crawl_output_dir"].endswith("/demo")


def test_reused_logical_source_can_reference_same_physical_output(tmp_path: Path):
    base = {
        "source_id": "aps",
        "logical_code": "APS",
        "name": "APS",
        "entrypoint": "https://www.aps.gob.bo",
        "host_key": "aps.gob.bo",
        "status": "GREEN_RESOURCES",
        "coverage_level": "L1_RESOURCE_DISCOVERY",
        "probe_profile": "HTTP_HTML_BASELINE",
        "resources_found": 12,
        "robots_http_codes": [200],
        "site_http_codes": [200],
        "request_trace": [],
        "requests_reported": 3,
        "execution_status": "SUCCESS",
        "stop_reason": "MAX_URLS",
        "elapsed_seconds": 3.0,
        "next_action": "candidate_for_b6_http_html",
        "error_excerpt": None,
        "reused_probe_from": None,
        "crawl_output_dir": ".runtime/source_mapping/crawls/aps",
        "log_file": ".runtime/source_mapping/logs/aps.log",
        "probe_config_file": ".runtime/source_mapping/probe_configs/aps.yaml",
    }
    reused = dict(base)
    reused.update(
        {
            "source_id": "aps_soat",
            "logical_code": "APS/SOAT",
            "name": "APS SOAT",
            "reused_probe_from": "APS",
        }
    )

    write_reports([base, reused], tmp_path, physical_probes=1)

    first = json.loads(
        (tmp_path / "logical_sources" / "aps.json").read_text(encoding="utf-8")
    )
    second = json.loads(
        (tmp_path / "logical_sources" / "aps_soat.json").read_text(encoding="utf-8")
    )
    assert first["crawl_output_dir"] == second["crawl_output_dir"]
    assert second["reused_probe_from"] == "APS"
