from __future__ import annotations

from apps.custom_probe.main import (
    classify_probe,
    json_data_shape,
    resource_kind,
    same_site,
)


def test_json_array_records_is_data():
    is_data, shape = json_data_shape(
        [{"x": 1}, {"x": 2}]
    )
    assert is_data is True
    assert shape == "LIST_RECORDS"


def test_metadata_object_is_not_data():
    is_data, shape = json_data_shape(
        {"name": "FIFA", "version": "1"}
    )
    assert is_data is False
    assert shape == "OBJECT_METADATA_OR_SMALL"


def test_same_site_accepts_subdomain():
    assert same_site(
        "https://api.example.org/data",
        "https://www.example.org",
    )


def test_structured_file_kind():
    assert (
        resource_kind("https://example.org/files/data.xlsx")
        == "STRUCTURED_FILE"
    )


def test_identity_unresolved_is_final_status():
    status, route = classify_probe(
        "IDENTITY_RESEARCH",
        identity_status="UNRESOLVED_NO_SAFE_SUCCESSOR",
    )
    assert status == "CUSTOM_IDENTITY_UNRESOLVED"
    assert route == "B10_STATUS"


def test_confirmed_json_data_promotes_custom():
    status, route = classify_probe(
        "API_SEMANTIC_PROBE",
        http_results=[
            {
                "skipped": False,
                "json_data": True,
                "resource_kind": "OTHER",
            }
        ],
    )
    assert status == "CUSTOM_DATA_ENDPOINT_CONFIRMED"
    assert route == "PROMOTE_CUSTOM_WORKFLOW"


def test_non_get_form_is_not_submitted_and_requires_policy_review():
    status, route = classify_probe(
        "SAFE_BROWSER_INTERACTION",
        browser_result={
            "structured_files": [],
            "discovered_files": [],
            "data_network_events": [],
            "blocked_non_get_forms": 1,
            "blocked_non_idempotent_requests": 0,
        },
    )
    assert status == "CUSTOM_REQUIRES_FORM_POLICY"
    assert route == "B8_POLICY_REVIEW"
