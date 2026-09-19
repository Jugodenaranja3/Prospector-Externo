"""Rerun selectivo y reanudable de fuentes afectadas por B10."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]

TERMINAL_STATES = {
    "SUCCESS",
    "EMPTY_REVIEW",
}


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML inválido: {path}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON inválido: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def latest_report(source_dir: Path) -> Path | None:
    reports = list(source_dir.glob("reports/run_*.json"))
    if not reports:
        return None
    return max(
        reports,
        key=lambda path: path.stat().st_mtime,
    )


def report_summary(path: Path) -> dict[str, Any]:
    data = load_json(path)
    rows = data.get("source_results") or []
    row = rows[0] if rows else {}

    coverage = row.get("coverage") or {}

    return {
        "run_id": data.get("run_id"),
        "sources_failed": data.get("sources_failed"),
        "source_id": row.get("source_id"),
        "workflow": row.get("workflow"),
        "execution_status": row.get("execution_status"),
        "content_status": row.get("content_status"),
        "failure_code": row.get("failure_code"),
        "resources_found": coverage.get("resources_found"),
        "pages_visited": coverage.get("pages_visited"),
        "stop_reason": coverage.get("stop_reason"),
        "report": str(path),
    }


def baseline_candidates(
    *,
    execution_map: dict[str, Any],
    baseline_root: Path,
) -> list[str]:
    candidates: list[str] = []

    for row in execution_map.get("sources", []):
        if not isinstance(row, dict):
            continue

        logical_id = row.get("logical_source_id")
        if not logical_id:
            continue

        report = latest_report(
            baseline_root / logical_id
        )

        if report is None:
            candidates.append(logical_id)
            continue

        summary = report_summary(report)

        if (
            summary["execution_status"] != "SUCCESS"
            or summary["content_status"] == "EMPTY_RESULT"
        ):
            candidates.append(logical_id)

    return candidates


def parse_source_ids(value: str | None) -> list[str] | None:
    if not value:
        return None
    result = []
    for item in value.split(","):
        clean = item.strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def load_state(
    path: Path,
    selected: list[str],
) -> dict[str, Any]:
    if path.exists():
        state = load_json(path)
    else:
        state = {
            "schema_version": "b10-targeted-remediation-1.0",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "sources": {},
        }

    rows = state.setdefault("sources", {})

    for source_id in selected:
        rows.setdefault(
            source_id,
            {
                "status": "PENDING",
                "attempts": 0,
            },
        )

    return state


def stream_process(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert process.stdout is not None

    with log_path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        for line in process.stdout:
            print(line, end="")
            handle.write(line)

    return int(process.wait())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rerun B10 solo para fuentes FAILED/EMPTY del baseline, "
            "con estado reanudable."
        )
    )
    parser.add_argument(
        "--sources-config",
        default="config/sources.yaml",
    )
    parser.add_argument(
        "--execution-map",
        default="config/source_execution_map.yaml",
    )
    parser.add_argument(
        "--baseline-root",
        default="output/b10-final/b10-final",
    )
    parser.add_argument(
        "--output-root",
        default="output/b10-remediation",
    )
    parser.add_argument(
        "--state",
        default=".runtime/b10_remediation/state.json",
    )
    parser.add_argument(
        "--source-ids",
        default=None,
        help="CSV opcional de logical_source_id.",
    )
    parser.add_argument(
        "--max-sources",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--resume",
        action="store_true",
    )
    parser.add_argument(
        "--rerun-empty",
        action="store_true",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    args = parser.parse_args()

    sources_path = PROJECT_ROOT / args.sources_config
    map_path = PROJECT_ROOT / args.execution_map
    baseline_root = PROJECT_ROOT / args.baseline_root
    output_root = PROJECT_ROOT / args.output_root
    state_path = PROJECT_ROOT / args.state

    sources_cfg = load_yaml(sources_path)
    execution_map = load_yaml(map_path)

    config_index = {
        row["source_id"]: row
        for row in sources_cfg.get("sources", [])
        if isinstance(row, dict) and row.get("source_id")
    }

    mapping_index = {
        row["logical_source_id"]: row
        for row in execution_map.get("sources", [])
        if isinstance(row, dict) and row.get("logical_source_id")
    }

    baseline = baseline_candidates(
        execution_map=execution_map,
        baseline_root=baseline_root,
    )

    requested = parse_source_ids(args.source_ids)

    if requested is None:
        selected = baseline
    else:
        unknown = [
            source_id
            for source_id in requested
            if source_id not in mapping_index
        ]
        if unknown:
            raise ValueError(
                "logical_source_id desconocido(s): "
                + ", ".join(unknown)
            )
        selected = requested

    if args.max_sources is not None:
        if args.max_sources <= 0:
            raise ValueError("--max-sources debe ser > 0")
        selected = selected[: args.max_sources]

    print("=" * 78)
    print("B10C — TARGETED REMEDIATION")
    print("=" * 78)
    print(f"Baseline affected: {len(baseline)}")
    print(f"Selected:          {len(selected)}")
    print("Sources:")
    for source_id in selected:
        print(f"  - {source_id}")

    if args.dry_run:
        print("Network:           NO (dry-run)")
        return 0

    state = load_state(state_path, selected)

    processed_now = 0

    for index, logical_id in enumerate(selected, 1):
        state_row = state["sources"][logical_id]
        previous = state_row.get("status")

        if args.resume and previous == "SUCCESS":
            print(f"[{index:02d}/{len(selected):02d}] {logical_id}: SKIP SUCCESS")
            continue

        if (
            args.resume
            and previous == "EMPTY_REVIEW"
            and not args.rerun_empty
        ):
            print(f"[{index:02d}/{len(selected):02d}] {logical_id}: SKIP EMPTY_REVIEW")
            continue

        mapping = mapping_index[logical_id]
        config_source_id = mapping.get("config_source_id")

        if config_source_id not in config_index:
            raise ValueError(
                f"{logical_id}: config físico no encontrado: {config_source_id}"
            )

        physical = dict(config_index[config_source_id])

        work_dir = (
            PROJECT_ROOT
            / ".runtime"
            / "b10_remediation"
            / "configs"
        )
        work_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        temp_config = work_dir / f"{logical_id}.yaml"
        temp_config.write_text(
            yaml.safe_dump(
                {"sources": [physical]},
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        logical_output = output_root / logical_id
        logical_output.mkdir(
            parents=True,
            exist_ok=True,
        )

        log_path = (
            PROJECT_ROOT
            / ".runtime"
            / "b10_remediation"
            / "logs"
            / f"{logical_id}.log"
        )

        command = [
            sys.executable,
            "-m",
            "apps.crawler_batch.main",
            "--config",
            str(temp_config),
            "--output-dir",
            str(logical_output),
            "--force",
        ]

        print()
        print(
            f"[{index:02d}/{len(selected):02d}] "
            f"{logical_id} -> {config_source_id}"
        )
        print("> " + subprocess.list2cmdline(command))

        state_row["status"] = "RUNNING"
        state_row["attempts"] = int(
            state_row.get("attempts", 0)
        ) + 1
        state_row["started_at_utc"] = (
            datetime.now(timezone.utc).isoformat()
        )
        atomic_json(state_path, state)

        return_code = stream_process(
            command,
            cwd=PROJECT_ROOT,
            log_path=log_path,
        )

        report = latest_report(logical_output)
        summary = (
            report_summary(report)
            if report is not None
            else {
                "execution_status": None,
                "content_status": None,
                "failure_code": "MISSING_REPORT",
                "resources_found": None,
                "pages_visited": None,
                "stop_reason": None,
                "report": None,
            }
        )

        if (
            return_code != 0
            or summary.get("execution_status") != "SUCCESS"
        ):
            status = "FAILED"
        elif summary.get("content_status") == "EMPTY_RESULT":
            status = "EMPTY_REVIEW"
        else:
            status = "SUCCESS"

        state_row.update(
            {
                "status": status,
                "return_code": return_code,
                "finished_at_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
                "summary": summary,
                "console_log": str(log_path),
            }
        )
        atomic_json(state_path, state)
        processed_now += 1

        print(
            f"RESULT {logical_id}: {status} | "
            f"exit={return_code} | "
            f"workflow={summary.get('workflow')} | "
            f"resources={summary.get('resources_found')} | "
            f"failure={summary.get('failure_code')} | "
            f"stop={summary.get('stop_reason')}"
        )

    selected_rows = [
        state["sources"][source_id]
        for source_id in selected
    ]

    counts: dict[str, int] = {}
    for row in selected_rows:
        status = str(row.get("status"))
        counts[status] = counts.get(status, 0) + 1

    print()
    print("=" * 78)
    print("B10C — TARGETED REMEDIATION SUMMARY")
    print("=" * 78)
    print(f"Processed now: {processed_now}")
    for status, count in sorted(counts.items()):
        print(f"  {status:<16} {count}")
    print(f"State: {state_path}")

    failures = counts.get("FAILED", 0)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
