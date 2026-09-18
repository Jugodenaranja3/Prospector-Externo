from __future__ import annotations

from apps.source_recovery.main import (
    candidate_variants,
    choose_best_variant,
    final_status,
)


def test_candidate_variants_include_www_bare_and_http_https():
    variants = candidate_variants("https://www.example.org")
    assert "https://www.example.org/" in variants
    assert "https://example.org/" in variants
    assert "http://www.example.org/" in variants
    assert "http://example.org/" in variants


def test_choose_best_prefers_http_2xx():
    best = choose_best_variant(
        [
            {"summary": "DNS_FAILURE", "variant": "https://a"},
            {"summary": "HTTP_5XX", "variant": "https://b"},
            {"summary": "HTTP_2XX", "variant": "https://c"},
        ]
    )
    assert best["variant"] == "https://c"


def test_final_status_dns():
    status, route = final_status({"summary": "DNS_FAILURE"})
    assert status == "DNS_FAILURE"
    assert route == "SOURCE_URL_REVIEW"


def test_final_status_access_restricted():
    status, route = final_status({"summary": "ACCESS_RESTRICTED"})
    assert status == "ACCESS_RESTRICTED"
    assert route == "ACCESS_REVIEW"
