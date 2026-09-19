from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


HEADER_RE = re.compile(
    r"^\[(?P<index>\d+)/(?P<total>\d+)\]\s+"
    r"(?P<code>.+?)\s+\((?P<source_id>[^)]+)\)\s*$",
    re.MULTILINE,
)

WORKFLOW_RE = re.compile(
    r"Iniciando\s+(?P<workflow>[A-Za-z_]+)Workflow",
    re.IGNORECASE,
)

RESOURCE_RE = re.compile(
    r"\((?P<count>\d+)\s+recursos\)",
    re.IGNORECASE,
)

RUN_ID_RE = re.compile(
    r"Proceso concluido\.\s+Run ID:\s+(?P<run_id>\S+)",
    re.IGNORECASE,
)

ERROR_PATTERNS = {
    "TRACEBACK": re.compile(r"Traceback \(most recent call last\):"),
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
    value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON inválido: {path}")
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(value, dict):
        raise ValueError(f"YAML inválido: {path}")
    return value


def normalize_workflow(name: str | None) -> str | None:
    if not name:
        return None

    compact = re.sub(r"[^a-z0-9]+", "", name.casefold())

    aliases = {
        "html": "html",
        "commentedhtml": "commented_html",
        "javascript": "javascript",
        "js": "javascript",
        "api": "api",
        "custom": "custom",
    }
    return aliases.get(compact, compact)


def split_source_segments(text: str) -> list[dict[str, Any]]:
    matches = list(HEADER_RE.finditer(text))
    result: list[dict[str, Any]] = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
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


def checkpoint_state_index(checkpoint: dict[str, Any]) -> dict[str, str]:
    sources = checkpoint.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("Checkpoint sin sources mapping")

    result = {}
    for source_id, row in sources.items():
        if not isinstance(row, dict):
            raise ValueError(f"{source_id}: checkpoint row inválida")
        state = row.get("state")
        if not isinstance(state, str):
            raise ValueError(f"{source_id}: checkpoint sin state")
        result[str(source_id)] = state
    return result


def matrix_index(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = matrix.get("sources")
    if not isinstance(rows, list):
        raise ValueError("final_source_matrix.yaml sin sources")

    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = row.get("source_id")
        if isinstance(source_id, str) and source_id:
            result[source_id] = row
    return result


def detect_error_signals(segment_text: str) -> list[str]:
    return [
        name
        for name, pattern in ERROR_PATTERNS.items()
        if pattern.search(segment_text)
    ]


def actual_workflow(segment_text: str) -> str | None:
    match = WORKFLOW_RE.search(segment_text)
    if not match:
        return None
    return normalize_workflow(match.group("workflow"))


def resource_count(segment_text: str) -> int | None:
    matches = list(RESOURCE_RE.finditer(segment_text))
    if not matches:
        return None
    return int(matches[-1].group("count"))


def child_run_id(segment_text: str) -> str | None:
    matches = list(RUN_ID_RE.finditer(segment_text))
    if not matches:
        return None
    return matches[-1].group("run_id")


def classify_source(
    *,
    source_id: str,
    expected_workflow: str | None,
    actual_workflow_value: str | None,
    checkpoint_state: str | None,
    errors: list[str],
    resources: int | None,
) -> tuple[str, bool]:
    if checkpoint_state != "SUCCEEDED":
        return "CHECKPOINT_NOT_SUCCEEDED", True

    if errors:
        return "PROCESS_OK_WITH_INTERNAL_ERROR", True

    if (
        expected_workflow
        and actual_workflow_value
        and expected_workflow != actual_workflow_value
    ):
        return "WORKFLOW_MISMATCH", True

    if expected_workflow and actual_workflow_value is None:
        return "WORKFLOW_NOT_OBSERVED", True

    if resources == 0:
        return "ZERO_RESOURCES_REVIEW", True

    if resources is None:
        return "RESOURCE_COUNT_NOT_OBSERVED", True

    return "CLEAN_SUCCESS", False


def audit(
    *,
    log_text: str,
    checkpoint: dict[str, Any],
    matrix: dict[str, Any],
) -> dict[str, Any]:
    if checkpoint.get("run_id") != "b10-final":
        raise ValueError(
            f"Checkpoint run_id={checkpoint.get('run_id')!r}; se esperaba 'b10-final'"
        )

    states = checkpoint_state_index(checkpoint)
    matrix_rows = matrix_index(matrix)

    operational = {
        source_id: row
        for source_id, row in matrix_rows.items()
        if row.get("execution_state") == "READY_FOR_B10_LIVE"
    }

    if len(operational) != 41:
        raise ValueError(
            f"Matriz final tiene {len(operational)} operacionales; se esperaban 41"
        )

    segments = split_source_segments(log_text)
    segment_index = {
        row["source_id"]: row
        for row in segments
    }

    rows: list[dict[str, Any]] = []

    for source_id, matrix_row in operational.items():
        segment = segment_index.get(source_id)
        expected = normalize_workflow(
            str(matrix_row.get("execution_workflow") or "")
        )

        if segment is None:
            rows.append(
                {
                    "source_id": source_id,
                    "logical_code": matrix_row.get("logical_code"),
                    "expected_workflow": expected,
                    "actual_workflow": None,
                    "checkpoint_state": states.get(source_id),
                    "resource_count": None,
                    "child_run_id": None,
                    "error_signals": ["MISSING_LOG_SEGMENT"],
                    "classification": "MISSING_LOG_SEGMENT",
                    "blocks_closure": True,
                }
            )
            continue

        errors = detect_error_signals(segment["text"])
        actual = actual_workflow(segment["text"])
        resources = resource_count(segment["text"])
        run_id = child_run_id(segment["text"])

        classification, blocks = classify_source(
            source_id=source_id,
            expected_workflow=expected,
            actual_workflow_value=actual,
            checkpoint_state=states.get(source_id),
            errors=errors,
            resources=resources,
        )

        rows.append(
            {
                "source_id": source_id,
                "logical_code": matrix_row.get("logical_code"),
                "expected_workflow": expected,
                "actual_workflow": actual,
                "checkpoint_state": states.get(source_id),
                "resource_count": resources,
                "child_run_id": run_id,
                "error_signals": errors,
                "classification": classification,
                "blocks_closure": blocks,
            }
        )

    classification_counts = Counter(
        row["classification"]
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

    succeeded_states = sum(
        1
        for source_id in operational
        if states.get(source_id) == "SUCCEEDED"
    )

    return {
        "schema_version": "b10-semantic-live-audit-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": checkpoint["run_id"],
        "operational_sources": len(operational),
        "log_segments_detected": len(segments),
        "checkpoint_succeeded_sources": succeeded_states,
        "classification_counts": dict(sorted(classification_counts.items())),
        "error_signal_counts": dict(sorted(error_signal_counts.items())),
        "closure_allowed": not blockers,
        "blocking_source_ids": blockers,
        "sources": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita semánticamente el run B10 live: errores internos, "
            "workflow real y conteos de recursos, sin confiar solo en exit code 0."
        )
    )
    parser.add_argument(
        "--console-log",
        default=".runtime/b10_live/b10-final-console.log",
    )
    parser.add_argument(
        "--checkpoint",
        default=".runtime/checkpoints/latest.json",
    )
    parser.add_argument(
        "--matrix",
        default="config/final_source_matrix.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/b10_semantic_audit",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="retorna exit code 2 si hay fuentes que bloquean cierre",
    )
    args = parser.parse_args()

    log_path = Path(args.console_log)
    checkpoint_path = Path(args.checkpoint)
    matrix_path = Path(args.matrix)

    if not log_path.exists():
        raise FileNotFoundError(log_path)
    if not checkpoint_path.exists():
        raise FileNotFoundError(checkpoint_path)
    if not matrix_path.exists():
        raise FileNotFoundError(matrix_path)

    report = audit(
        log_text=log_path.read_text(
            encoding="utf-8",
            errors="replace",
        ),
        checkpoint=load_json(checkpoint_path),
        matrix=load_yaml(matrix_path),
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "latest.json"
    json_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    md_lines = [
        "# B10B.1 — Semantic Live Audit",
        "",
        f"- Run ID: **{report['run_id']}**",
        f"- Operational sources: **{report['operational_sources']}**",
        f"- Log segments: **{report['log_segments_detected']}**",
        f"- Checkpoint SUCCEEDED: **{report['checkpoint_succeeded_sources']}**",
        f"- Closure allowed: **{report['closure_allowed']}**",
        "",
        "## Classifications",
        "",
        "| Classification | Count |",
        "|---|---:|",
    ]

    for key, count in report["classification_counts"].items():
        md_lines.append(f"| {key} | {count} |")

    md_lines.extend(
        [
            "",
            "## Blocking sources",
            "",
        ]
    )

    for row in report["sources"]:
        if not row["blocks_closure"]:
            continue
        md_lines.append(
            f"- `{row['source_id']}` — {row['classification']} "
            f"(expected={row['expected_workflow']}, "
            f"actual={row['actual_workflow']}, "
            f"resources={row['resource_count']}, "
            f"errors={row['error_signals']})"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(md_lines) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print("B10B.1 — SEMANTIC LIVE AUDIT")
    print("=" * 78)
    print(f"Run ID:                {report['run_id']}")
    print(f"Operational sources:   {report['operational_sources']}")
    print(f"Log segments detected: {report['log_segments_detected']}")
    print(f"Checkpoint SUCCEEDED:  {report['checkpoint_succeeded_sources']}")
    print()
    print("Classifications:")
    for key, count in report["classification_counts"].items():
        print(f"  {key:<34} {count}")
    print()
    print("Error signals:")
    if report["error_signal_counts"]:
        for key, count in report["error_signal_counts"].items():
            print(f"  {key:<34} {count}")
    else:
        print("  none")
    print()
    print(f"Closure allowed:       {report['closure_allowed']}")
    print(f"Blocking sources:      {len(report['blocking_source_ids'])}")

    for row in report["sources"]:
        if not row["blocks_closure"]:
            continue
        print(
            f"  - {row['source_id']}: {row['classification']} | "
            f"expected={row['expected_workflow']} "
            f"actual={row['actual_workflow']} "
            f"resources={row['resource_count']} "
            f"errors={row['error_signals']}"
        )

    print()
    print(f"JSON: {json_path}")
    print(f"MD:   {output_dir / 'latest.md'}")

    if args.strict and not report["closure_allowed"]:
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
