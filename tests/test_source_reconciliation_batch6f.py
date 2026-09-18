from __future__ import annotations

from apps.source_reconciliation.main import (
    RETRY_ACTION,
    allowed_hosts,
    build_probe,
)


def test_allowed_hosts_includes_www_and_bare():
    hosts = allowed_hosts("https://cadexco.bo/")
    assert "cadexco.bo" in hosts
    assert "www.cadexco.bo" in hosts


def test_retry_probe_uses_candidate_entrypoint_and_keeps_robots():
    row = {
        "source_id": "cadexco",
        "logical_code": "CADEXCO",
        "candidate_entrypoint": "https://cadexco.bo/",
    }
    source = build_probe(row, 18, 55)["sources"][0]
    assert source["entrypoint"] == "https://cadexco.bo/"
    assert source["ignore_robots_txt"] is False
    assert source["discover_sitemaps"] is True
    assert source["discover_apis"] is True


def test_retry_action_constant():
    assert RETRY_ACTION == "RETRY_CRAWL"
