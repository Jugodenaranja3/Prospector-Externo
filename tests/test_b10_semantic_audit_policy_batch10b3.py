from __future__ import annotations

from apps.b10_semantic_audit.main import (
    classify_source,
)


def test_missing_workflow_signal_is_warning_not_failure():
    classification, blocks, severity = classify_source(
        expected_workflow="javascript",
        actual_workflow_value=None,
        checkpoint_state="SUCCEEDED",
        errors=[],
        resources=None,
    )

    assert classification == "WORKFLOW_NOT_OBSERVED_WARNING"
    assert blocks is False
    assert severity == "WARNING"


def test_missing_resource_count_is_warning_not_failure():
    classification, blocks, severity = classify_source(
        expected_workflow="html",
        actual_workflow_value="html",
        checkpoint_state="SUCCEEDED",
        errors=[],
        resources=None,
    )

    assert classification == "RESOURCE_COUNT_NOT_OBSERVED_WARNING"
    assert blocks is False
    assert severity == "WARNING"


def test_zero_resources_blocks_even_without_workflow_log():
    classification, blocks, severity = classify_source(
        expected_workflow="commented_html",
        actual_workflow_value=None,
        checkpoint_state="SUCCEEDED",
        errors=[],
        resources=0,
    )

    assert classification == "ZERO_RESOURCES_REVIEW"
    assert blocks is True
    assert severity == "REVIEW"


def test_observed_workflow_mismatch_blocks():
    classification, blocks, severity = classify_source(
        expected_workflow="custom",
        actual_workflow_value="html",
        checkpoint_state="SUCCEEDED",
        errors=[],
        resources=0,
    )

    assert classification == "WORKFLOW_MISMATCH"
    assert blocks is True
    assert severity == "ERROR"


def test_internal_error_blocks_before_observability():
    classification, blocks, severity = classify_source(
        expected_workflow="html",
        actual_workflow_value="html",
        checkpoint_state="SUCCEEDED",
        errors=["TRACEBACK"],
        resources=None,
    )

    assert classification == "PROCESS_OK_WITH_INTERNAL_ERROR"
    assert blocks is True
    assert severity == "ERROR"
