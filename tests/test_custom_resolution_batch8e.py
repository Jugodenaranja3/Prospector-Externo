from __future__ import annotations

from apps.custom_resolution.main import (
    apply_resolution,
    confirmed_endpoints,
    resolve_one,
)


def test_confirmed_endpoints_keeps_real_data_only():
    row = {
        "http_results": [
            {
                "url": "https://example.org/meta",
                "final_url": "https://example.org/meta",
                "skipped": False,
                "json_data": False,
                "resource_kind": "OTHER",
            },
            {
                "url": "https://example.org/data",
                "final_url": "https://example.org/data",
                "skipped": False,
                "json_data": True,
                "resource_kind": "OTHER",
            },
        ]
    }
    assert confirmed_endpoints(row) == ["https://example.org/data"]


def test_custom_data_promotes():
    result = resolve_one(
        {"source_id": "fifa", "logical_code": "FIFA"},
        {
            "status": "CUSTOM_DATA_ENDPOINT_CONFIRMED",
            "http_results": [
                {
                    "url": "https://example.org/data",
                    "final_url": "https://example.org/data",
                    "skipped": False,
                    "json_data": True,
                    "resource_kind": "OTHER",
                }
            ],
        },
        None,
        None,
        {},
        {},
    )
    assert result["resolution_status"] == "OPERATIONAL_CUSTOM_DATA_API"
    assert result["next_phase"] == "OPERATIONAL_CONFIG"


def test_curated_html_promotes():
    result = resolve_one(
        {"source_id": "cadexco", "logical_code": "CADEXCO"},
        {"status": "CUSTOM_REQUIRES_FORM_POLICY"},
        {
            "status": "RESOLVED_CURATED_HTTP_SEEDS",
            "seed_results": [
                {
                    "seed": "https://example.org/publicaciones/",
                    "status_code": 200,
                    "final_url": "https://example.org/publicaciones/",
                }
            ],
            "discovered_urls": ["https://example.org/report.pdf"],
        },
        None,
        {},
        {},
    )
    assert result["resolution_status"] == "OPERATIONAL_HTTP_HTML_CURATED"
    assert result["workflow_strategy"] == "html"


def test_transtats_promotes_without_post():
    result = resolve_one(
        {"source_id": "transtats", "logical_code": "TRANSTATS"},
        {"status": "CUSTOM_REQUIRES_FORM_POLICY"},
        {"status": "READ_ONLY_POST_QUERY_CANDIDATES"},
        None,
        {"confirmed": True},
        {
            "allowed_methods": ["GET", "HEAD"],
            "submission_policy": "metadata_only_no_post",
            "resource_url_patterns": ["/DL_SelectFields.aspx"],
            "evidence_seed_urls": [
                "https://example.org/DL_SelectFields.aspx?x=1"
            ],
        },
    )
    assert result["resolution_status"] == "OPERATIONAL_CUSTOM_FORM_RESOURCE"
    assert (
        result["operational_config"]["submission_policy"]
        == "metadata_only_no_post"
    )


def test_identity_unresolved_goes_b10():
    result = resolve_one(
        {"source_id": "bolcereales", "logical_code": "BOLCEREALES"},
        {"status": "CUSTOM_IDENTITY_UNRESOLVED"},
        None,
        None,
        {},
        {},
    )
    assert result["resolution_status"] == "IDENTITY_UNRESOLVED"
    assert result["next_phase"] == "B10_STATUS"


def test_overlay_closes_b8():
    plan = {
        "schema_version": "source-operational-plan-1.0",
        "browser_sources_resolved": 11,
        "sources": [],
    }

    for i in range(38):
        plan["sources"].append(
            {
                "source_id": f"op{i}",
                "operational_status": "OPERATIONAL_HTTP_HTML",
                "workflow_strategy": "html",
                "next_phase": "OPERATIONAL_CONFIG",
            }
        )

    for i in range(6):
        plan["sources"].append(
            {
                "source_id": f"b10{i}",
                "operational_status": "ACCESS_RESTRICTED",
                "workflow_strategy": None,
                "next_phase": "B10_STATUS",
            }
        )

    for i in range(8):
        plan["sources"].append(
            {
                "source_id": f"c{i}",
                "operational_status": "B8_CUSTOM_CANDIDATE",
                "workflow_strategy": None,
                "next_phase": "B8_CUSTOM",
            }
        )

    overlays = []
    for i in range(8):
        operational = i < 3
        overlays.append(
            {
                "source_id": f"c{i}",
                "resolution_status": (
                    "OPERATIONAL_CUSTOM_DATA_API"
                    if operational
                    else "NO_PUBLIC_DATA_EVIDENCE"
                ),
                "workflow_strategy": (
                    "custom" if operational else None
                ),
                "next_phase": (
                    "OPERATIONAL_CONFIG"
                    if operational
                    else "B10_STATUS"
                ),
                "effective_entrypoint_override": None,
                "decision_note": "fixture",
                "b8_probe_status": "fixture",
                "operational_config": (
                    {"custom_kind": "fixture"}
                    if operational
                    else None
                ),
            }
        )

    updated = apply_resolution(plan, {"sources": overlays})

    assert updated["b8_closed"] is True
    assert updated["summary_by_next_phase"]["OPERATIONAL_CONFIG"] == 41
    assert updated["summary_by_next_phase"]["B10_STATUS"] == 11
    assert all(
        row["next_phase"] != "B8_CUSTOM"
        for row in updated["sources"]
    )
