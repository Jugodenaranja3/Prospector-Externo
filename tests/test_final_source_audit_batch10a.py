from __future__ import annotations

import pytest

from apps.final_source_audit.main import (
    build_final_matrix,
    classify_workflow_pair,
    compatibility_index,
    validate_closures,
)


def compatibility_fixture():
    return {
        "logical_to_physical": {
            "html": [
                "html",
                "commented_html",
            ],
            "javascript": ["javascript"],
            "api": ["api"],
            "custom": ["custom"],
        }
    }


def fixtures():
    plan_rows = []
    map_rows = []
    physical_rows = []

    for i in range(52):
        operational = i < 41
        source_id = f"s{i}"

        plan_rows.append(
            {
                "source_id": source_id,
                "logical_code": f"S{i}",
                "name": f"Source {i}",
                "effective_entrypoint": (
                    f"https://example.org/{i}"
                ),
                "operational_status": (
                    "OPERATIONAL_HTTP_HTML"
                    if operational
                    else "NO_PUBLIC_DATA_EVIDENCE"
                ),
                "workflow_strategy": (
                    "html"
                    if operational
                    else None
                ),
                "next_phase": (
                    "OPERATIONAL_CONFIG"
                    if operational
                    else "B10_STATUS"
                ),
            }
        )

        if operational:
            config_id = f"p{i % 35}"
            map_rows.append(
                {
                    "logical_source_id": (
                        source_id
                    ),
                    "config_source_id": (
                        config_id
                    ),
                }
            )

    for i in range(35):
        physical_rows.append(
            {
                "source_id": f"p{i}",
                "entrypoint": (
                    "https://example.org/"
                    f"physical/{i}"
                ),
                "workflow": (
                    "commented_html"
                    if i == 1
                    else "html"
                ),
            }
        )

    # Force the logical source mapped to p1
    # to remain logical html. This models BBV.
    plan = {
        "b8_closed": True,
        "summary_by_next_phase": {
            "OPERATIONAL_CONFIG": 41,
            "B10_STATUS": 11,
        },
        "sources": plan_rows,
    }

    execution_map = {
        "logical_operational_sources": 41,
        "physical_config_sources": 35,
        "sources": map_rows,
    }

    sources_cfg = {
        "sources": physical_rows,
    }

    persistence = {
        "b9_closed": True,
    }

    return (
        plan,
        execution_map,
        sources_cfg,
        persistence,
    )


def test_html_to_commented_html_is_compatible_specialization():
    compat = compatibility_index(
        compatibility_fixture()
    )
    assert (
        classify_workflow_pair(
            "html",
            "commented_html",
            compat,
        )
        == "COMPATIBLE_SPECIALIZATION"
    )


def test_html_to_javascript_is_incompatible():
    compat = compatibility_index(
        compatibility_fixture()
    )
    assert (
        classify_workflow_pair(
            "html",
            "javascript",
            compat,
        )
        == "INCOMPATIBLE"
    )


def test_final_matrix_accepts_commented_html_specialization():
    (
        plan,
        execution_map,
        sources_cfg,
        _,
    ) = fixtures()

    matrix, counts = build_final_matrix(
        plan,
        execution_map,
        sources_cfg,
        compatibility_fixture(),
    )

    assert len(matrix) == 52
    assert counts[
        "operational_sources"
    ] == 41
    assert counts[
        "status_only_sources"
    ] == 11
    assert counts[
        "physical_config_sources"
    ] == 35
    assert counts[
        "workflow_relations"
    ]["COMPATIBLE_SPECIALIZATION"] >= 1


def test_incompatible_physical_workflow_fails():
    (
        plan,
        execution_map,
        sources_cfg,
        _,
    ) = fixtures()

    sources_cfg["sources"][0][
        "workflow"
    ] = "javascript"

    with pytest.raises(
        ValueError,
        match="incompatibles",
    ):
        build_final_matrix(
            plan,
            execution_map,
            sources_cfg,
            compatibility_fixture(),
        )


def test_status_only_has_no_execution_mapping():
    (
        plan,
        execution_map,
        sources_cfg,
        _,
    ) = fixtures()

    matrix, _ = build_final_matrix(
        plan,
        execution_map,
        sources_cfg,
        compatibility_fixture(),
    )

    status_rows = [
        row
        for row in matrix
        if row["execution_state"]
        == "NOT_EXECUTED_BY_POLICY"
    ]

    assert len(status_rows) == 11
    assert all(
        row["config_source_id"] is None
        for row in status_rows
    )


def test_closures_require_b8_b9_and_counts():
    (
        plan,
        execution_map,
        _,
        persistence,
    ) = fixtures()

    result = validate_closures(
        plan,
        persistence,
        execution_map,
    )

    assert result["b8_closed"] is True
    assert result["b9_closed"] is True
    assert (
        result["operational_config"]
        == 41
    )
    assert result["b10_status"] == 11
