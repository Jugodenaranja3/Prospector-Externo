from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

HEADER_RE = re.compile(
    r"\[(?P<index>\d+)\s*/\s*(?P<total>\d+)\]\s+"
    r"(?P<code>[^\r\n]+?)\s+\((?P<source_id>[^)\r\n]+)\)"
)

WORKFLOW_PATTERNS = (
    re.compile(
        r"Iniciando\s+(?P<workflow>[A-Za-z_]+)Workflow",
        re.IGNORECASE,
    ),
    re.compile(
        r"workflow\s+(?P<workflow>html|commented_html|javascript|api|custom)",
        re.IGNORECASE,
    ),
)

RESOURCE_PATTERNS = (
    re.compile(
        r"\((?P<count>\d+)\s+recursos\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"resource_count['\"=: ]+(?P<count>\d+)",
        re.IGNORECASE,
    ),
)

RUN_ID_RE = re.compile(
    r"Proceso concluido\.\s+Run ID:\s+(?P<run_id>\S+)",
    re.IGNORECASE,
)

ERROR_PATTERNS = {
    "TRACEBACK": re.compile(
        r"Traceback \(most recent call last\):",
        re.IGNORECASE,
    ),
    "UNCONTROLLED_WORKFLOW_ERROR": re.compile(
        r"Fallo no controlado en workflow",
        re.IGNORECASE,
    ),
    "PARSER_REJECTED_MARKUP": re.compile(
        r"ParserRejectedMarkup",
        re.IGNORECASE,
    ),
    "UNICODE_ENCODE_ERROR": re.compile(
        r"UnicodeEncodeError",
        re.IGNORECASE,
    ),
    "LOGGING_ERROR": re.compile(
        r"--- Logging error ---",
        re.IGNORECASE,
    ),
    "EXPLICIT_ERROR_LOG": re.compile(
        r"\[(?:ERROR|CRITICAL)\]",
        re.IGNORECASE,
    ),
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    )
    if not isinstance(value, dict):
        raise ValueError(f"JSON inválido: {path}")
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(
        path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    )
    if not isinstance(value, dict):
        raise ValueError(f"YAML inválido: {path}")
    return value


def normalize_text(text: str) -> str:
    text = ANSI_RE.sub("", text)
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\x00", "")
    )


def header_matches(text: str) -> list[re.Match[str]]:
    return list(HEADER_RE.finditer(text))


def workflow_signal_count(text: str) -> int:
    return sum(
        len(pattern.findall(text))
        for pattern in WORKFLOW_PATTERNS
    )


def decode_candidate_score(text: str) -> tuple[int, int, int, int]:
    normalized = normalize_text(text)
    headers = len(header_matches(normalized))
    workflows = workflow_signal_count(normalized)
    replacements = normalized.count("\ufffd")
    controls = sum(
        1
        for char in normalized
        if ord(char) < 32 and char not in "\n\t"
    )
    return (
        headers,
        workflows,
        -replacements,
        -controls,
    )


def decode_console_log(path: Path) -> tuple[str, dict[str, Any]]:
    raw = path.read_bytes()
    if not raw:
        raise ValueError("Console log vacío")

    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = normalize_text(
            raw.decode("utf-16", errors="replace")
        )
        return text, {
            "encoding": "utf-16-bom",
            "bytes": len(raw),
            "header_candidates": len(header_matches(text)),
            "workflow_signals": workflow_signal_count(text),
            "bom": True,
        }

    if raw.startswith(b"\xef\xbb\xbf"):
        text = normalize_text(
            raw.decode("utf-8-sig", errors="replace")
        )
        return text, {
            "encoding": "utf-8-sig",
            "bytes": len(raw),
            "header_candidates": len(header_matches(text)),
            "workflow_signals": workflow_signal_count(text),
            "bom": True,
        }

    candidates: list[tuple[str, str]] = []

    try:
        utf8_text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        utf8_text = None

    if utf8_text is not None:
        normalized = normalize_text(utf8_text)
        headers = len(header_matches(normalized))
        if headers > 0:
            return normalized, {
                "encoding": "utf-8",
                "bytes": len(raw),
                "header_candidates": headers,
                "workflow_signals": workflow_signal_count(normalized),
                "bom": False,
            }
        candidates.append(("utf-8", utf8_text))

    for encoding in ("utf-16-le", "utf-16-be", "cp1252"):
        try:
            decoded = raw.decode(
                encoding,
                errors="replace",
            )
        except Exception:
            continue
        candidates.append((encoding, decoded))

    if not candidates:
        raise ValueError(
            "No fue posible decodificar console log"
        )

    scored = []
    for encoding, text in candidates:
        normalized = normalize_text(text)
        scored.append(
            (
                decode_candidate_score(normalized),
                encoding,
                normalized,
            )
        )

    scored.sort(
        key=lambda item: item[0],
        reverse=True,
    )
    score, encoding, text = scored[0]

    return text, {
        "encoding": encoding,
        "bytes": len(raw),
        "header_candidates": score[0],
        "workflow_signals": score[1],
        "bom": False,
    }


def normalize_workflow(
    name: str | None,
) -> str | None:
    if not name:
        return None

    compact = re.sub(
        r"[^a-z0-9]+",
        "",
        name.casefold(),
    )

    aliases = {
        "html": "html",
        "commentedhtml": "commented_html",
        "javascript": "javascript",
        "js": "javascript",
        "api": "api",
        "custom": "custom",
    }

    return aliases.get(compact, compact)


def split_source_segments(
    text: str,
) -> list[dict[str, Any]]:
    matches = header_matches(text)
    result = []

    for index, match in enumerate(matches):
        start = match.start()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        )
        result.append(
            {
                "index": int(match.group("index")),
                "total": int(match.group("total")),
                "logical_code": match.group("code").strip(),
                "source_id": match.group("source_id").strip(),
                "text": text[start:end],
            }
        )

    return result


def checkpoint_state_index(
    checkpoint: dict[str, Any],
) -> dict[str, str]:
    sources = checkpoint.get("sources")

    if not isinstance(sources, dict):
        raise ValueError(
            "Checkpoint sin sources mapping"
        )

    result = {}

    for source_id, row in sources.items():
        if not isinstance(row, dict):
            raise ValueError(
                f"{source_id}: checkpoint row inválida"
            )

        state = row.get("state")
        if not isinstance(state, str):
            raise ValueError(
                f"{source_id}: checkpoint sin state"
            )

        result[str(source_id)] = state

    return result


def matrix_index(
    matrix: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    rows = matrix.get("sources")

    if not isinstance(rows, list):
        raise ValueError(
            "final_source_matrix.yaml sin sources"
        )

    result = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        source_id = row.get("source_id")
        if isinstance(source_id, str) and source_id:
            result[source_id] = row

    return result


def detect_error_signals(
    segment_text: str,
) -> list[str]:
    return [
        name
        for name, pattern in ERROR_PATTERNS.items()
        if pattern.search(segment_text)
    ]


def actual_workflow(
    segment_text: str,
) -> str | None:
    for pattern in WORKFLOW_PATTERNS:
        match = pattern.search(segment_text)
        if match:
            return normalize_workflow(
                match.group("workflow")
            )
    return None


def resource_count(
    segment_text: str,
) -> int | None:
    candidates = []

    for pattern in RESOURCE_PATTERNS:
        candidates.extend(
            pattern.finditer(segment_text)
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item.start()
    )

    return int(
        candidates[-1].group("count")
    )


def child_run_id(
    segment_text: str,
) -> str | None:
    matches = list(
        RUN_ID_RE.finditer(segment_text)
    )

    if not matches:
        return None

    return matches[-1].group("run_id")


def classify_source(
    *,
    expected_workflow: str | None,
    actual_workflow_value: str | None,
    checkpoint_state: str | None,
    errors: list[str],
    resources: int | None,
) -> tuple[str, bool, str]:
    """
    Returns (classification, blocks_closure, severity).

    Policy:
    - Negative evidence blocks.
    - Missing observability does not equal failure.
    - Zero resources is concrete evidence and requires review.
    """
    if checkpoint_state != "SUCCEEDED":
        return (
            "CHECKPOINT_NOT_SUCCEEDED",
            True,
            "ERROR",
        )

    if errors:
        return (
            "PROCESS_OK_WITH_INTERNAL_ERROR",
            True,
            "ERROR",
        )

    if (
        expected_workflow
        and actual_workflow_value
        and expected_workflow
        != actual_workflow_value
    ):
        return (
            "WORKFLOW_MISMATCH",
            True,
            "ERROR",
        )

    # Concrete zero-resource evidence is stronger than a missing workflow log.
    if resources == 0:
        return (
            "ZERO_RESOURCES_REVIEW",
            True,
            "REVIEW",
        )

    # Absence of a workflow log line is an observability gap, not proof that
    # the configured workflow did not execute.
    if (
        expected_workflow
        and actual_workflow_value is None
    ):
        return (
            "WORKFLOW_NOT_OBSERVED_WARNING",
            False,
            "WARNING",
        )

    # Likewise, no explicit resource count in console is not evidence of zero.
    if resources is None:
        return (
            "RESOURCE_COUNT_NOT_OBSERVED_WARNING",
            False,
            "WARNING",
        )

    return (
        "CLEAN_SUCCESS",
        False,
        "OK",
    )


def audit(
    *,
    log_text: str,
    checkpoint: dict[str, Any],
    matrix: dict[str, Any],
    decode_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if checkpoint.get("run_id") != "b10-final":
        raise ValueError(
            "Checkpoint run_id="
            f"{checkpoint.get('run_id')!r}; "
            "se esperaba 'b10-final'"
        )

    states = checkpoint_state_index(
        checkpoint
    )
    matrix_rows = matrix_index(matrix)

    operational = {
        source_id: row
        for source_id, row in matrix_rows.items()
        if row.get("execution_state")
        == "READY_FOR_B10_LIVE"
    }

    if len(operational) != 41:
        raise ValueError(
            "Matriz final tiene "
            f"{len(operational)} operacionales; "
            "se esperaban 41"
        )

    segments = split_source_segments(
        log_text
    )

    segment_index = {}

    for row in segments:
        source_id = row["source_id"]
        if source_id in segment_index:
            raise ValueError(
                "Segmento duplicado en log para "
                f"{source_id}"
            )
        segment_index[source_id] = row

    rows = []

    for source_id, matrix_row in operational.items():
        segment = segment_index.get(source_id)
        expected = normalize_workflow(
            str(
                matrix_row.get(
                    "execution_workflow"
                )
                or ""
            )
        )

        if segment is None:
            rows.append(
                {
                    "source_id": source_id,
                    "logical_code": matrix_row.get(
                        "logical_code"
                    ),
                    "expected_workflow": expected,
                    "actual_workflow": None,
                    "checkpoint_state": states.get(
                        source_id
                    ),
                    "resource_count": None,
                    "child_run_id": None,
                    "error_signals": [
                        "MISSING_LOG_SEGMENT"
                    ],
                    "classification": (
                        "MISSING_LOG_SEGMENT"
                    ),
                    "severity": "ERROR",
                    "blocks_closure": True,
                }
            )
            continue

        errors = detect_error_signals(
            segment["text"]
        )
        actual = actual_workflow(
            segment["text"]
        )
        resources = resource_count(
            segment["text"]
        )
        run_id = child_run_id(
            segment["text"]
        )

        (
            classification,
            blocks,
            severity,
        ) = classify_source(
            expected_workflow=expected,
            actual_workflow_value=actual,
            checkpoint_state=states.get(
                source_id
            ),
            errors=errors,
            resources=resources,
        )

        rows.append(
            {
                "source_id": source_id,
                "logical_code": matrix_row.get(
                    "logical_code"
                ),
                "expected_workflow": expected,
                "actual_workflow": actual,
                "checkpoint_state": states.get(
                    source_id
                ),
                "resource_count": resources,
                "child_run_id": run_id,
                "error_signals": errors,
                "classification": classification,
                "severity": severity,
                "blocks_closure": blocks,
            }
        )

    classification_counts = Counter(
        row["classification"]
        for row in rows
    )

    severity_counts = Counter(
        row["severity"]
        for row in rows
    )

    error_signal_counts = Counter(
        signal
        for row in rows
        for signal in row["error_signals"]
    )

    blockers = [
        row["source_id"]
        for row in rows
        if row["blocks_closure"]
    ]

    warnings = [
        row["source_id"]
        for row in rows
        if row["severity"] == "WARNING"
    ]

    succeeded_states = sum(
        1
        for source_id in operational
        if states.get(source_id) == "SUCCEEDED"
    )

    return {
        "schema_version": (
            "b10-semantic-live-audit-1.3"
        ),
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "run_id": checkpoint["run_id"],
        "console_log_decode": (
            decode_meta or {}
        ),
        "operational_sources": len(
            operational
        ),
        "log_segments_detected": len(
            segments
        ),
        "checkpoint_succeeded_sources": (
            succeeded_states
        ),
        "classification_counts": dict(
            sorted(
                classification_counts.items()
            )
        ),
        "severity_counts": dict(
            sorted(
                severity_counts.items()
            )
        ),
        "error_signal_counts": dict(
            sorted(
                error_signal_counts.items()
            )
        ),
        "closure_allowed": not blockers,
        "blocking_source_ids": blockers,
        "warning_source_ids": warnings,
        "sources": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita semánticamente el run B10 live "
            "distinguiendo fallos reales de gaps de observabilidad."
        )
    )

    parser.add_argument(
        "--console-log",
        default=(
            ".runtime/b10_live/"
            "b10-final-console.log"
        ),
    )
    parser.add_argument(
        "--checkpoint",
        default=(
            ".runtime/checkpoints/latest.json"
        ),
    )
    parser.add_argument(
        "--matrix",
        default=(
            "config/final_source_matrix.yaml"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=(
            ".runtime/b10_semantic_audit"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
    )

    args = parser.parse_args()

    log_path = Path(
        args.console_log
    )
    checkpoint_path = Path(
        args.checkpoint
    )
    matrix_path = Path(
        args.matrix
    )

    for path in (
        log_path,
        checkpoint_path,
        matrix_path,
    ):
        if not path.exists():
            raise FileNotFoundError(path)

    log_text, decode_meta = (
        decode_console_log(log_path)
    )

    report = audit(
        log_text=log_text,
        checkpoint=load_json(
            checkpoint_path
        ),
        matrix=load_yaml(
            matrix_path
        ),
        decode_meta=decode_meta,
    )

    output_dir = Path(
        args.output_dir
    )
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_path = (
        output_dir / "latest.json"
    )
    json_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    md_lines = [
        "# B10B.3 — Semantic Evidence Policy",
        "",
        f"- Run ID: **{report['run_id']}**",
        f"- Log segments: **{report['log_segments_detected']}**",
        f"- Checkpoint SUCCEEDED: **{report['checkpoint_succeeded_sources']}**",
        f"- Closure allowed: **{report['closure_allowed']}**",
        "",
        "## Classifications",
        "",
        "| Classification | Count |",
        "|---|---:|",
    ]

    for key, count in report[
        "classification_counts"
    ].items():
        md_lines.append(
            f"| {key} | {count} |"
        )

    md_lines.extend(
        [
            "",
            "## True blockers",
            "",
        ]
    )

    for row in report["sources"]:
        if not row["blocks_closure"]:
            continue

        md_lines.append(
            f"- `{row['source_id']}` — "
            f"{row['classification']} "
            f"(expected={row['expected_workflow']}, "
            f"actual={row['actual_workflow']}, "
            f"resources={row['resource_count']}, "
            f"errors={row['error_signals']})"
        )

    md_lines.extend(
        [
            "",
            "## Observability warnings",
            "",
        ]
    )

    for row in report["sources"]:
        if row["severity"] != "WARNING":
            continue
        md_lines.append(
            f"- `{row['source_id']}` — "
            f"{row['classification']}"
        )

    (
        output_dir / "latest.md"
    ).write_text(
        "\n".join(md_lines) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print(
        "B10B.3 — SEMANTIC EVIDENCE RECONCILIATION"
    )
    print("=" * 78)
    print(
        f"Console encoding:      "
        f"{report['console_log_decode'].get('encoding')}"
    )
    print(
        f"Log segments detected: "
        f"{report['log_segments_detected']}"
    )
    print(
        f"Checkpoint SUCCEEDED:  "
        f"{report['checkpoint_succeeded_sources']}"
    )
    print()

    print("Classifications:")
    for key, count in report[
        "classification_counts"
    ].items():
        print(
            f"  {key:<42} {count}"
        )

    print()
    print("Severity:")
    for key, count in report[
        "severity_counts"
    ].items():
        print(
            f"  {key:<12} {count}"
        )

    print()
    print(
        f"True blockers:         "
        f"{len(report['blocking_source_ids'])}"
    )

    for row in report["sources"]:
        if not row["blocks_closure"]:
            continue

        print(
            f"  - {row['source_id']}: "
            f"{row['classification']} | "
            f"expected={row['expected_workflow']} "
            f"actual={row['actual_workflow']} "
            f"resources={row['resource_count']} "
            f"errors={row['error_signals']}"
        )

    print()
    print(
        f"Observability warnings:"
        f" {len(report['warning_source_ids'])}"
    )

    for row in report["sources"]:
        if row["severity"] != "WARNING":
            continue
        print(
            f"  - {row['source_id']}: "
            f"{row['classification']}"
        )

    print()
    print(
        f"Closure allowed:       "
        f"{report['closure_allowed']}"
    )
    print(f"JSON: {json_path}")
    print(
        f"MD:   "
        f"{output_dir / 'latest.md'}"
    )

    if (
        args.strict
        and not report[
            "closure_allowed"
        ]
    ):
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
