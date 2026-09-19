from __future__ import annotations

from pathlib import Path

from apps.b10_semantic_audit.main import (
    audit,
    decode_console_log,
    header_matches,
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
                "execution_workflow": (
                    "html"
                    if operational
                    else None
                ),
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
            "state": (
                "SUCCEEDED"
                if i < 41
                else "SKIPPED_STATUS"
            )
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
                    (
                        "2026 [INFO] "
                        "prospector.workflows.html: "
                        "Iniciando HtmlWorkflow "
                        f"async para [s{i}]"
                    ),
                    (
                        "2026 [INFO] "
                        "prospector.application.catalog: "
                        "Checkpoint incremental persistido "
                        f"con éxito para [s{i}] "
                        f"({i + 1} recursos)."
                    ),
                    (
                        "2026 [INFO] "
                        "prospector.batch.main: "
                        "Proceso concluido. "
                        f"Run ID: child_{i}"
                    ),
                ]
            )
        )

    return "\n".join(chunks)


def test_header_parser_is_not_line_anchor_dependent():
    sample = "python : [01/41] ANAPO (anapo)\n"
    matches = header_matches(sample)
    assert len(matches) == 1
    assert matches[0].group("source_id") == "anapo"


def test_utf16le_powershell_log_is_detected(tmp_path: Path):
    path = tmp_path / "console.log"
    payload = clean_log()
    path.write_bytes(
        b"\xff\xfe"
        + payload.encode("utf-16-le")
    )

    text, meta = decode_console_log(
        path
    )

    assert meta["header_candidates"] == 41
    assert "[01/41] S0 (s0)" in text


def test_utf8_log_is_detected(tmp_path: Path):
    path = tmp_path / "console.log"
    payload = clean_log()
    path.write_bytes(
        payload.encode("utf-8")
    )

    text, meta = decode_console_log(
        path
    )

    assert meta["encoding"] == "utf-8"
    assert meta["header_candidates"] == 41
    assert "[41/41]" in text


def test_utf16le_without_bom_is_detected(tmp_path: Path):
    path = tmp_path / "console.log"
    payload = clean_log()
    path.write_bytes(
        payload.encode("utf-16-le")
    )

    text, meta = decode_console_log(
        path
    )

    assert meta["encoding"] == "utf-16-le"
    assert meta["header_candidates"] == 41
    assert "[01/41]" in text


def test_clean_run_allows_closure():
    report = audit(
        log_text=clean_log(),
        checkpoint=checkpoint_fixture(),
        matrix=matrix_fixture(),
    )

    assert report["closure_allowed"] is True
    assert (
        report["classification_counts"][
            "CLEAN_SUCCESS"
        ]
        == 41
    )


def test_internal_traceback_blocks():
    log = clean_log().replace(
        (
            "Checkpoint incremental persistido "
            "con éxito para [s3] (4 recursos)."
        ),
        (
            "Traceback (most recent call last):\n"
            "ParserRejectedMarkup\n"
            "Checkpoint incremental persistido "
            "con éxito para [s3] (4 recursos)."
        ),
    )

    report = audit(
        log_text=log,
        checkpoint=checkpoint_fixture(),
        matrix=matrix_fixture(),
    )

    row = next(
        item
        for item in report["sources"]
        if item["source_id"] == "s3"
    )

    assert (
        row["classification"]
        == "PROCESS_OK_WITH_INTERNAL_ERROR"
    )
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

    assert report[
        "closure_allowed"
    ] is False
    assert (
        report["classification_counts"][
            "ZERO_RESOURCES_REVIEW"
        ]
        == 1
    )


def test_workflow_normalization():
    assert (
        normalize_workflow("Html")
        == "html"
    )
    assert (
        normalize_workflow(
            "CommentedHtml"
        )
        == "commented_html"
    )
    assert (
        normalize_workflow(
            "Javascript"
        )
        == "javascript"
    )
