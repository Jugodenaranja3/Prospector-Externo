from __future__ import annotations

from apps.browser_characterization.main import (
    classify_result,
    is_data_network_event,
    is_download_url,
    normalize_href,
)


def test_download_url_detection():
    assert is_download_url("https://example.org/files/data.xlsx")
    assert is_download_url("https://example.org/files/report.pdf?x=1")
    assert not is_download_url("https://example.org/page")


def test_data_network_event_by_content_type():
    assert is_data_network_event(
        "https://example.org/anything",
        "application/json; charset=utf-8",
    )


def test_data_network_event_by_url():
    assert is_data_network_event(
        "https://example.org/api/datasets/42",
        "text/plain",
    )


def test_classification_prefers_direct_files():
    status, route = classify_result(
        navigation_ok=True,
        direct_downloads=2,
        data_network_events=4,
        xhr_fetch_events=10,
    )
    assert status == "BROWSER_FILE_DISCOVERY"
    assert route == "B7_OPERATIONAL_BROWSER"


def test_no_browser_gain_routes_to_custom():
    status, route = classify_result(
        navigation_ok=True,
        direct_downloads=0,
        data_network_events=0,
        xhr_fetch_events=0,
    )
    assert status == "BROWSER_NO_DATA_EVIDENCE"
    assert route == "B8_CUSTOM_REVIEW"


def test_normalize_href_rejects_javascript():
    assert normalize_href("https://example.org", "javascript:void(0)") is None
    assert (
        normalize_href("https://example.org/base/", "../file.csv")
        == "https://example.org/file.csv"
    )
