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

# Intentionally not anchored. PowerShell can prefix native stderr records
# (for example "python : ..."), and some captured lines may contain spaces
# or terminal decorations before the runner header.
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
    text = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\x00", "")
    )
    return text


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

    # Header evidence must dominate every other heuristic.
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

    candidates: list[tuple[str, str]] = []

    # BOM is authoritative when present.
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
        normalized = normalize_text(text)
        return normalized, {
            "encoding": "utf-16-bom",
            "bytes": len(raw),
            "header_candidates": len(header_matches(normalized)),
            "workflow_signals": workflow_signal_count(normalized),
            "bom": True,
        }

    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig", errors="replace")
        normalized = normalize_text(text)
        return normalized, {
            "encoding": "utf-8-sig",
            "bytes": len(raw),
            "header_candidates": len(header_matches(normalized)),
            "workflow_signals": workflow_signal_count(normalized),
            "bom": True,
        }

    # First prefer strict UTF-8 when it contains actual runner headers.
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

    # If UTF-8 has no structural evidence, evaluate Windows-oriented
    # alternatives. This handles UTF-16LE logs without BOM from Tee-Object.
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

    scored: list[
        tuple[tuple[int, int, int, int], str, str]
    ] = []

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
    result: list[dict[str, Any]] = []

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
) -> tuple[str, bool]:
    if checkpoint_state != "SUCCEEDED":
        return (
            "CHECKPOINT_NOT_SUCCEEDED",
            True,
        )

    if errors:
        return (
            "PROCESS_OK_WITH_INTERNAL_ERROR",
            True,
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
        )

    if (
        expected_workflow
        and actual_workflow_value is None
    ):
        return (
            "WORKFLOW_NOT_OBSERVED",
            True,
        )

    if resources == 0:
        return (
            "ZERO_RESOURCES_REVIEW",
            True,
        )

    if resources is None:
        return (
            "RESOURCE_COUNT_NOT_OBSERVED",
            True,
        )

    return ("CLEAN_SUCCESS", False)


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

    rows: list[dict[str, Any]] = []

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

        classification, blocks = classify_source(
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
        "schema_version": (
            "b10-semantic-live-audit-1.2"
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
        "error_signal_counts": dict(
            sorted(
                error_signal_counts.items()
            )
        ),
        "closure_allowed": not blockers,
        "blocking_source_ids": blockers,
        "sources": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita semánticamente el run "
            "B10 live sin confiar solo "
            "en exit code 0."
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
        "# B10B.2.1 — Semantic Live Audit",
        "",
        (
            "- Run ID: "
            f"**{report['run_id']}**"
        ),
        (
            "- Console encoding: "
            f"**{report['console_log_decode'].get('encoding')}**"
        ),
        (
            "- Operational sources: "
            f"**{report['operational_sources']}**"
        ),
        (
            "- Log segments: "
            f"**{report['log_segments_detected']}**"
        ),
        (
            "- Checkpoint SUCCEEDED: "
            f"**{report['checkpoint_succeeded_sources']}**"
        ),
        (
            "- Closure allowed: "
            f"**{report['closure_allowed']}**"
        ),
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
            "## Blocking sources",
            "",
        ]
    )

    for row in report["sources"]:
        if not row["blocks_closure"]:
            continue

        md_lines.append(
            f"- `{row['source_id']}` — "
            f"{row['classification']} "
            f"(expected="
            f"{row['expected_workflow']}, "
            f"actual="
            f"{row['actual_workflow']}, "
            f"resources="
            f"{row['resource_count']}, "
            f"errors="
            f"{row['error_signals']})"
        )

    (
        output_dir / "latest.md"
    ).write_text(
        "\n".join(md_lines) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print(
        "B10B.2.1 — SEMANTIC LIVE AUDIT "
        "(ROBUST LOG PARSER)"
    )
    print("=" * 78)
    print(
        f"Console encoding:      "
        f"{report['console_log_decode'].get('encoding')}"
    )
    print(
        f"Log bytes:             "
        f"{report['console_log_decode'].get('bytes')}"
    )
    print(
        f"Header candidates:     "
        f"{report['console_log_decode'].get('header_candidates')}"
    )
    print(
        f"Run ID:                "
        f"{report['run_id']}"
    )
    print(
        f"Operational sources:   "
        f"{report['operational_sources']}"
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
            f"  {key:<34} {count}"
        )

    print()
    print("Error signals:")
    if report["error_signal_counts"]:
        for key, count in report[
            "error_signal_counts"
        ].items():
            print(
                f"  {key:<34} {count}"
            )
    else:
        print("  none")

    print()
    print(
        f"Closure allowed:       "
        f"{report['closure_allowed']}"
    )
    print(
        f"Blocking sources:      "
        f"{len(report['blocking_source_ids'])}"
    )

    for row in report["sources"]:
        if not row["blocks_closure"]:
            continue

        print(
            f"  - {row['source_id']}: "
            f"{row['classification']} | "
            f"expected="
            f"{row['expected_workflow']} "
            f"actual="
            f"{row['actual_workflow']} "
            f"resources="
            f"{row['resource_count']} "
            f"errors="
            f"{row['error_signals']}"
        )

    print()
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
