from __future__ import annotations

from apps.custom_policy_probe.main import (
    extract_links,
    same_site,
)


def test_same_site_accepts_subdomain():
    assert same_site(
        "https://api.example.org/x",
        "https://www.example.org",
    )


def test_extract_links_resolves_relative_urls():
    html = '<a href="/files/report.pdf">PDF</a>'
    assert extract_links(
        html,
        "https://example.org/base/",
    ) == ["https://example.org/files/report.pdf"]


def test_extract_links_ignores_mailto_and_javascript():
    html = (
        '<a href="mailto:a@example.org">m</a>'
        '<a href="javascript:void(0)">j</a>'
    )
    assert extract_links(html, "https://example.org") == []
