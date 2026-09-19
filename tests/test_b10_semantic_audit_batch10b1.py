from __future__ import annotations

from apps.b10_semantic_audit.main import (
    audit,
    classify_source,
    normalize_workflow,
)


def matrix_fixture():
    rows = []
    for i in range(52):
        operational = i < 41
        rows.append(
            {
                "source_id": f"s{i}",
                "logical_code": f"S{i}",
                "execution_workflow": "html" if operational else None,
                "execution_state": (
                    "READY_FOR_B10_LIVE"
                    if operational
                    else "NOT_EXECUTED_BY_POLICY"
                ),
            }
        )
    return {"sources": rows}


def checkpoint_fixture():
    sources = {}
    for i in range(52):
        sources[f"s{i}"] = {
            "state": "SUCCEEDED" if i < 41 else "SKIPPED_STATUS"
        }
    return {
        "run_id": "b10-final",
        "sources": sources,
    }


def clean_log():
    chunks = []
    for i in range(41):
        chunks.append(
            "\n".join(
                [
                    f"[{i + 1:02d}/41] S{i} (s{i})",
                    f"2026 [INFO] prospector.workflows.html: Iniciando HtmlWorkflow async para [s{i}]",
                    f"2026 [INFO] prospector.application.catalog: Checkpoint incremental persistido con éxito para [s{i}] ({i + 1} recursos).",
                    f"2026 [INFO] prospector.batch.main: Proceso concluido. Run ID: child_{i}",
                ]
            )
        )
    return "\n".join(chunks)


def test_clean_run_allows_closure():
    report = audit(
        log_text=clean_log(),
        checkpoint=checkpoint_fixture(),
        matrix=matrix_fixture(),
    )

    assert report["closure_allowed"] is True
    assert report["classification_counts"]["CLEAN_SUCCESS"] == 41


def test_internal_traceback_blocks_even_when_checkpoint_succeeded():
    log = clean_log().replace(
        "2026 [INFO] prospector.application.catalog: Checkpoint incremental persistido con éxito para [s3] (4 recursos).",
        "Traceback (most recent call last):\nParserRejectedMarkup\n"
        "2026 [INFO] prospector.application.catalog: Checkpoint incremental persistido con éxito para [s3] (4 recursos).",
    )

    report = audit(
        log_text=log,
        checkpoint=checkpoint_fixture(),
        matrix=matrix_fixture(),
    )

    row = next(
        item for item in report["sources"] if item["source_id"] == "s3"
    )
    assert row["classification"] == "PROCESS_OK_WITH_INTERNAL_ERROR"
    assert row["blocks_closure"] is True
    assert report["closure_allowed"] is False


def test_workflow_mismatch_blocks():
    log = clean_log().replace(
        "Iniciando HtmlWorkflow async para [s5]",
        "Iniciando JavascriptWorkflow async para [s5]",
    )

    report = audit(
        log_text=log,
        checkpoint=checkpoint_fixture(),
        matrix=matrix_fixture(),
    )

    row = next(
        item for item in report["sources"] if item["source_id"] == "s5"
    )
    assert row["classification"] == "WORKFLOW_MISMATCH"
    assert row["blocks_closure"] is True


def test_zero_resources_requires_review():
    log = clean_log().replace(
        "(7 recursos)",
        "(0 recursos)",
        1,
    )

    report = audit(
        log_text=log,
        checkpoint=checkpoint_fixture(),
        matrix=matrix_fixture(),
    )

    assert report["closure_allowed"] is False
    assert report["classification_counts"]["ZERO_RESOURCES_REVIEW"] == 1


def test_workflow_normalization():
    assert normalize_workflow("Html") == "html"
    assert normalize_workflow("CommentedHtml") == "commented_html"
    assert normalize_workflow("Javascript") == "javascript"


def test_classifier_checkpoint_failure_precedes_other_signals():
    classification, blocks = classify_source(
        source_id="s",
        expected_workflow="html",
        actual_workflow_value="javascript",
        checkpoint_state="FAILED",
        errors=["TRACEBACK"],
        resources=0,
    )
    assert classification == "CHECKPOINT_NOT_SUCCEEDED"
    assert blocks is True
