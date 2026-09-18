from __future__ import annotations

from apps.source_diagnostics.main import diagnose


def test_deeper_review_is_not_called_javascript_automatically():
    reason, route = diagnose(
        "REACHABLE_NEEDS_DEEPER_REVIEW",
        [],
        "",
    )
    assert reason == "NO_RESOURCES_AFTER_HTML_API"
    assert route == "B7_OR_CUSTOM_REVIEW"


def test_access_restricted_keeps_access_semantics():
    reason, route = diagnose(
        "ACCESS_RESTRICTED",
        [{"site_http_codes": [307, 308, 403]}],
        "403 Forbidden",
    )
    assert reason == "HTTP_ACCESS_RESTRICTED"
    assert route == "ACCESS_REVIEW"


def test_dns_error_is_classified():
    reason, route = diagnose(
        "EXECUTION_ERROR",
        [],
        "ConnectError: [Errno 11001] getaddrinfo failed",
    )
    assert reason == "DNS_OR_NAME_RESOLUTION"
    assert route == "SOURCE_URL_REVIEW"


def test_unknown_error_remains_explicit():
    reason, route = diagnose(
        "EXECUTION_ERROR",
        [],
        "something unexplained",
    )
    assert reason == "UNKNOWN_EXECUTION_ERROR"
    assert route == "MANUAL_DIAGNOSTIC_REVIEW"
