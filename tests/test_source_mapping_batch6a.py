from __future__ import annotations

from pathlib import Path

from apps.source_mapping.main import (
    build_probe_config,
    classify_probe,
    load_inventory,
    normalized_entrypoint,
)

ROOT = Path(__file__).resolve().parents[1]


def test_inventory_has_52_logical_sources_and_46_unique_entrypoints():
    sources = load_inventory(ROOT / "config" / "source_inventory.yaml")
    assert len(sources) == 52
    assert len({normalized_entrypoint(s["entrypoint"]) for s in sources}) == 46
    assert len({s["host_key"] for s in sources}) == 46


def test_inventory_source_ids_are_unique():
    sources = load_inventory(ROOT / "config" / "source_inventory.yaml")
    ids = [s["source_id"] for s in sources]
    assert len(ids) == len(set(ids))


def test_known_shared_entrypoints_are_preserved_not_merged():
    sources = load_inventory(ROOT / "config" / "source_inventory.yaml")
    by_code = {s["logical_code"]: s for s in sources}
    assert by_code["APS"]["entrypoint"] == by_code["APS/SOAT"]["entrypoint"]
    assert by_code["BCB"]["entrypoint"] == by_code["ASFI - BCB"]["entrypoint"]
    assert by_code["ICCO"]["entrypoint"] == by_code["FDTA-Valles"]["entrypoint"]
    assert by_code["APS"]["source_id"] != by_code["APS/SOAT"]["source_id"]


def test_probe_config_is_bounded_and_cheap():
    source = {
        "source_id": "demo",
        "logical_code": "DEMO",
        "name": "Demo",
        "entrypoint": "https://www.example.org",
        "host_key": "example.org",
    }
    data = build_probe_config(
        source,
        max_requests=5,
        max_runtime_seconds=20,
        rate_limit_seconds=1.0,
        max_depth=1,
        max_urls=40,
    )
    cfg = data["sources"][0]
    assert cfg["workflow"] == "html"
    assert cfg["max_requests"] == 5
    assert cfg["max_runtime_seconds"] == 20
    assert cfg["max_depth"] == 1
    assert cfg["max_urls"] == 40
    assert cfg["discover_sitemaps"] is False
    assert cfg["discover_apis"] is False
    assert cfg["probe_api_documentation"] is False
    assert cfg["ignore_robots_txt"] is False


def test_classification_green_with_resources():
    status, action = classify_probe(
        returncode=0,
        log_text='HTTP Request: GET https://x "HTTP/1.1 200 OK"',
        resources_found=3,
        execution_status="SUCCESS",
        timed_out=False,
    )
    assert status == "GREEN_RESOURCES"
    assert action == "candidate_for_b6_http_html"


def test_classification_reachable_without_resources():
    status, action = classify_probe(
        returncode=0,
        log_text='HTTP Request: GET https://x "HTTP/1.1 200 OK"',
        resources_found=0,
        execution_status="SUCCESS",
        timed_out=False,
    )
    assert status == "REACHABLE_NO_RESOURCES"
    assert action == "review_seed_or_workflow"


def test_classification_403():
    status, _ = classify_probe(
        returncode=1,
        log_text='HTTP Request: GET https://x "HTTP/1.1 403 Forbidden"',
        resources_found=0,
        execution_status=None,
        timed_out=False,
    )
    assert status == "HTTP_403"


def test_classification_timeout():
    status, _ = classify_probe(
        returncode=124,
        log_text="",
        resources_found=0,
        execution_status=None,
        timed_out=True,
    )
    assert status == "TIMEOUT"
