from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_AUDIT = Path(".runtime/b11_output_audit/latest.json")
DEFAULT_STATE = Path(".runtime/b11_bulk_projection/latest.json")
DEFAULT_LOG_DIR = Path(".runtime/b11_bulk_projection/logs")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = audit.get("sources")
    if not isinstance(rows, list):
        raise ValueError("B11 audit no contiene sources:list")

    selected = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("classification") == "RAW_READY_NO_PROJECTION"
    ]

    return sorted(
        selected,
        key=lambda row: str(row.get("logical_source_id") or ""),
    )


def resolve_projection_source_id(row: dict[str, Any], evidence_root: Path) -> str:
    physical = row.get("physical_source_id")
    if isinstance(physical, str) and physical.strip():
        return physical.strip()

    sources_json = evidence_root / "state" / "sources.json"
    if sources_json.exists():
        try:
            raw = load_json(sources_json)
        except Exception:
            raw = None
        if isinstance(raw, dict) and len(raw) == 1:
            only = next(iter(raw))
            if isinstance(only, str) and only.strip():
                return only.strip()

    logical = row.get("logical_source_id")
    if isinstance(logical, str) and logical.strip():
        return logical.strip()

    raise ValueError("No se pudo resolver source_id para proyección")


def choose_grouping_contract(
    repo_root: Path,
    logical_source_id: str,
    physical_source_id: str,
) -> Path | None:
    candidates = [
        repo_root / "config" / "grouping" / f"{logical_source_id}.yaml",
        repo_root / "config" / "grouping" / f"{logical_source_id}.yml",
    ]

    if physical_source_id != logical_source_id:
        candidates.extend(
            [
                repo_root / "config" / "grouping" / f"{physical_source_id}.yaml",
                repo_root / "config" / "grouping" / f"{physical_source_id}.yml",
            ]
        )

    for path in candidates:
        if path.exists():
            return path

    return None


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def legacy_records(document: Any) -> int:
    required = {
        "descripcion",
        "url_descarga",
        "fecha_actualizacion",
        "tipo_archivo",
        "url_origen",
        "metodo_deteccion",
    }

    if not isinstance(document, dict) or "ESTADISTICAS" not in document:
        return 0

    count = 0
    for row in walk_dicts(document["ESTADISTICAS"]):
        if required.issubset(set(row)):
            count += 1
    return count


def scan_legacy(evidence_root: Path) -> tuple[int, list[str]]:
    total = 0
    files: list[str] = []

    if not evidence_root.exists():
        return 0, files

    for path in evidence_root.rglob("*.json"):
        try:
            raw = load_json(path)
        except Exception:
            continue

        count = legacy_records(raw)
        if count > 0:
            total += count
            files.append(str(path))

    return total, sorted(files)


def run_projection(
    *,
    repo_root: Path,
    row: dict[str, Any],
    log_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    logical_source_id = str(row["logical_source_id"])
    evidence_root = Path(str(row["evidence_root"]))

    if not evidence_root.is_absolute():
        evidence_root = repo_root / evidence_root

    projection_source_id = resolve_projection_source_id(
        row,
        evidence_root,
    )

    grouping_contract = choose_grouping_contract(
        repo_root,
        logical_source_id,
        projection_source_id,
    )

    before_records, before_files = scan_legacy(evidence_root)

    command = [
        sys.executable,
        "-m",
        "apps.datax_projection.main",
        "--output-dir",
        str(evidence_root),
        "--source",
        projection_source_id,
    ]

    if grouping_contract is not None:
        command.extend(
            [
                "--grouping-contract",
                str(grouping_contract),
            ]
        )

    result = {
        "logical_source_id": logical_source_id,
        "projection_source_id": projection_source_id,
        "evidence_root": str(evidence_root),
        "grouping_contract": (
            str(grouping_contract)
            if grouping_contract is not None
            else None
        ),
        "raw_resource_count": row.get("raw_resource_count"),
        "high_priority_resources": row.get("high_priority_resources"),
        "before_legacy_records": before_records,
        "before_legacy_files": before_files,
        "command": command,
        "returncode": None,
        "after_legacy_records": before_records,
        "after_legacy_files": before_files,
        "status": "DRY_RUN" if dry_run else "PENDING",
        "log_path": None,
    }

    if dry_run:
        return result

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{logical_source_id}.log"

    cp = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    output = []
    output.append("$ " + subprocess.list2cmdline(command))
    output.append("")
    if cp.stdout:
        output.append(cp.stdout.rstrip())
    if cp.stderr:
        output.append(cp.stderr.rstrip())
    log_path.write_text(
        "\n".join(output) + "\n",
        encoding="utf-8",
    )

    after_records, after_files = scan_legacy(evidence_root)

    if cp.returncode != 0:
        status = "COMMAND_FAILED"
    elif after_records > 0:
        status = "PROJECTED"
    else:
        status = "EMPTY_PROJECTION"

    result.update(
        {
            "returncode": cp.returncode,
            "after_legacy_records": after_records,
            "after_legacy_files": after_files,
            "status": status,
            "log_path": str(log_path),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B11B: proyección DATAX bulk, offline, sobre outputs B10."
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=DEFAULT_AUDIT,
    )
    parser.add_argument(
        "--state-output",
        type=Path,
        default=DEFAULT_STATE,
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
    )
    parser.add_argument(
        "--source-ids",
        default="",
        help="CSV opcional de logical_source_id.",
    )
    parser.add_argument(
        "--max-sources",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )
    args = parser.parse_args()

    repo_root = Path(".").resolve()

    audit_path = (
        args.audit
        if args.audit.is_absolute()
        else repo_root / args.audit
    )
    state_output = (
        args.state_output
        if args.state_output.is_absolute()
        else repo_root / args.state_output
    )
    log_dir = (
        args.log_dir
        if args.log_dir.is_absolute()
        else repo_root / args.log_dir
    )

    audit = load_json(audit_path)
    rows = candidate_rows(audit)

    requested = {
        item.strip()
        for item in args.source_ids.split(",")
        if item.strip()
    }
    if requested:
        rows = [
            row
            for row in rows
            if row.get("logical_source_id") in requested
        ]

    if args.max_sources is not None:
        if args.max_sources <= 0:
            raise ValueError("--max-sources debe ser > 0")
        rows = rows[: args.max_sources]

    print("=" * 78)
    print("B11B — BULK OFFLINE DATAX PROJECTION")
    print("=" * 78)
    print(f"Selected:          {len(rows)}")
    print(f"Network:           NO")
    print(f"Crawl:             NO")
    print(f"Dry-run:           {'YES' if args.dry_run else 'NO'}")

    results: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=1):
        logical = str(row.get("logical_source_id"))
        result = run_projection(
            repo_root=repo_root,
            row=row,
            log_dir=log_dir,
            dry_run=args.dry_run,
        )
        results.append(result)

        if args.dry_run:
            print(
                f"[{index:02}/{len(rows):02}] {logical} -> "
                f"{result['projection_source_id']} | "
                f"grouping={result['grouping_contract'] or '-'}"
            )
        else:
            print(
                f"[{index:02}/{len(rows):02}] {logical}: "
                f"{result['status']} | "
                f"legacy={result['after_legacy_records']} | "
                f"rc={result['returncode']}"
            )

    counts = Counter(result["status"] for result in results)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "selected": len(rows),
        "counts": dict(sorted(counts.items())),
        "results": results,
    }

    state_output.parent.mkdir(parents=True, exist_ok=True)
    state_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 78)
    print("B11B — PROJECTION SUMMARY")
    print("=" * 78)
    print(f"Processed:         {len(results)}")
    for key in (
        "PROJECTED",
        "EMPTY_PROJECTION",
        "COMMAND_FAILED",
        "DRY_RUN",
    ):
        print(f"  {key:<18} {counts.get(key, 0)}")
    print(f"State: {state_output.relative_to(repo_root)}")

    # Per-source failures are findings for the next batch. The runner itself
    # only fails if orchestration/audit state is invalid.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
