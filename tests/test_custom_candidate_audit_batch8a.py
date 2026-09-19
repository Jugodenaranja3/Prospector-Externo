from __future__ import annotations

from apps.custom_candidate_audit.main import (
    choose_strategy,
    classify_candidate,
)


def test_identity_candidate_class():
    row = {
        "b6_status": "IDENTITY_REVIEW_REQUIRED",
        "b7_status": None,
    }
    assert classify_candidate(row) == "IDENTITY_RESEARCH_REQUIRED"


def test_api_semantic_candidate_class():
    row = {
        "b6_status": "API_SEMANTIC_REVIEW",
        "b7_status": None,
    }
    assert classify_candidate(row) == "API_SEMANTIC_REVIEW"


def test_browser_network_review_class():
    row = {
        "b6_status": "B7_B8_CANDIDATE",
        "b7_status": "JAVASCRIPT_NETWORK_REVIEW",
    }
    assert (
        classify_candidate(row)
        == "BROWSER_NETWORK_SEMANTIC_REVIEW"
    )


def test_browser_custom_with_forms_uses_safe_interaction():
    browser = {
        "html_signals": {
            "forms": 1,
            "selects": 0,
            "buttons": 2,
        },
        "keyword_links": [],
    }
    assert (
        choose_strategy(
            "BROWSER_INTERACTION_REVIEW",
            browser,
            {},
        )
        == "SAFE_BROWSER_INTERACTION"
    )


def test_browser_custom_with_keyword_links_uses_traversal():
    browser = {
        "html_signals": {
            "forms": 0,
            "selects": 0,
            "buttons": 0,
        },
        "keyword_links": [
            "https://example.org/statistics",
        ],
    }
    assert (
        choose_strategy(
            "BROWSER_INTERACTION_REVIEW",
            browser,
            {},
        )
        == "DATA_LINK_TRAVERSAL"
    )


def test_browser_custom_without_signals_stays_custom():
    browser = {
        "html_signals": {
            "forms": 0,
            "selects": 0,
            "buttons": 0,
        },
        "keyword_links": [],
    }
    assert (
        choose_strategy(
            "BROWSER_INTERACTION_REVIEW",
            browser,
            {},
        )
        == "CUSTOM_SITE_REVIEW"
    )
