from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_AUDIT = Path(".runtime/b11_output_audit/latest.json")
DEFAULT_OUTPUT = Path(".runtime/b11_closure/latest.json")

EXPECTED_COUNTS = {
    "DATAX_READY": 38,
    "ACQUISITION_JOB_READY": 1,
    "RAW_READY_NO_PROJECTION": 0,
    "SUCCESS_EMPTY": 0,
    "EXTERNAL_BLOCKER": 2,
    "EXECUTION_NOT_SUCCESS": 0,
    "EVIDENCE_MISSING": 0,
}

EXPECTED_EXTERNAL_BLOCKERS = {"mhe", "sigma"}
EXPECTED_ACQUISITION_JOBS = {"transtats"}
OPERATIONAL_TOTAL = 41


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def source_rows(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = audit.get("sources")
    if not isinstance(rows, list):
        raise ValueError("El audit B11 no contiene sources:list")

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Fila B11 inválida")
        source_id = row.get("logical_source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("Fila B11 sin logical_source_id")
        if source_id in result:
            raise ValueError(f"Fuente duplicada en audit B11: {source_id}")
        result[source_id] = row
    return result


def evaluate(audit: dict[str, Any]) -> dict[str, Any]:
    scope = audit.get("scope")
    summary = audit.get("summary")

    if not isinstance(scope, dict) or not isinstance(summary, dict):
        raise ValueError("El audit B11 no contiene scope/summary válidos")

    operational_sources = scope.get("operational_sources")
    if operational_sources != OPERATIONAL_TOTAL:
        raise ValueError(
            f"Conteo operacional inesperado: {operational_sources}"
        )

    counts = summary.get("classification_counts")
    if not isinstance(counts, dict):
        raise ValueError("classification_counts ausente")

    normalized_counts = {
        key: int(counts.get(key, 0))
        for key in EXPECTED_COUNTS
    }

    rows = source_rows(audit)

    external_blockers = {
        source_id
        for source_id, row in rows.items()
        if row.get("classification") == "EXTERNAL_BLOCKER"
    }
    acquisition_jobs = {
        source_id
        for source_id, row in rows.items()
        if row.get("classification") == "ACQUISITION_JOB_READY"
    }
    unresolved = {
        source_id: str(row.get("classification"))
        for source_id, row in rows.items()
        if row.get("classification")
        in {
            "RAW_READY_NO_PROJECTION",
            "SUCCESS_EMPTY",
            "EXECUTION_NOT_SUCCESS",
            "EVIDENCE_MISSING",
        }
    }

    checks: dict[str, bool] = {}

    checks["classification_counts"] = (
        normalized_counts == EXPECTED_COUNTS
    )
    checks["external_blockers_exact"] = (
        external_blockers == EXPECTED_EXTERNAL_BLOCKERS
    )
    checks["acquisition_job_exact"] = (
        acquisition_jobs == EXPECTED_ACQUISITION_JOBS
    )
    checks["no_unresolved"] = not unresolved
    checks["all_operational_rows_present"] = len(rows) == OPERATIONAL_TOTAL

    omc = rows.get("omc", {})
    checks["omc_datax_ready"] = (
        omc.get("classification") == "DATAX_READY"
        and omc.get("evidence_origin") == "b11_refresh"
        and int(omc.get("raw_resource_count") or 0) > 0
        and int(omc.get("legacy_projection_records") or 0) > 0
    )

    transtats = rows.get("transtats", {})
    manifest_path = transtats.get("special_downstream_manifest")
    checks["transtats_acquisition_semantics"] = (
        transtats.get("classification") == "ACQUISITION_JOB_READY"
        and transtats.get("special_downstream_decision")
        == "ACQUISITION_JOB_READY"
        and isinstance(manifest_path, str)
        and bool(manifest_path)
    )

    accounted = (
        normalized_counts["DATAX_READY"]
        + normalized_counts["ACQUISITION_JOB_READY"]
        + normalized_counts["EXTERNAL_BLOCKER"]
    )
    checks["operational_accounting"] = accounted == OPERATIONAL_TOTAL

    failures = [
        name
        for name, ok in checks.items()
        if not ok
    ]

    closure_status = (
        "CLOSED_WITH_EXTERNAL_BLOCKERS"
        if not failures
        else "OPEN"
    )

    return {
        "closure_status": closure_status,
        "checks": checks,
        "failed_checks": failures,
        "classification_counts": normalized_counts,
        "operational": {
            "total": OPERATIONAL_TOTAL,
            "datax_ready": normalized_counts["DATAX_READY"],
            "acquisition_job_ready": normalized_counts[
                "ACQUISITION_JOB_READY"
            ],
            "external_blocked": normalized_counts["EXTERNAL_BLOCKER"],
            "accounted": accounted,
        },
        "external_blockers": sorted(external_blockers),
        "acquisition_jobs": sorted(acquisition_jobs),
        "unresolved": unresolved,
        "omc": {
            "classification": omc.get("classification"),
            "evidence_origin": omc.get("evidence_origin"),
            "raw_resource_count": omc.get("raw_resource_count"),
            "legacy_projection_records": omc.get(
                "legacy_projection_records"
            ),
        },
        "transtats": {
            "classification": transtats.get("classification"),
            "special_downstream_decision": transtats.get(
                "special_downstream_decision"
            ),
            "manifest": manifest_path,
        },
        "totals": {
            "raw_resources": summary.get("total_raw_resources"),
            "legacy_records": summary.get(
                "total_legacy_projection_records"
            ),
            "high_priority_resources": summary.get(
                "total_high_priority_resources"
            ),
            "medium_priority_resources": summary.get(
                "total_medium_priority_resources"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditor final de cierre B11."
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=DEFAULT_AUDIT,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    repo_root = Path(".").resolve()
    audit_path = (
        args.audit
        if args.audit.is_absolute()
        else repo_root / args.audit
    )
    output_path = (
        args.output
        if args.output.is_absolute()
        else repo_root / args.output
    )

    audit = load_json(audit_path)
    report = evaluate(audit)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["audit_path"] = str(audit_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print("B11 — FINAL DOWNSTREAM READINESS CLOSURE")
    print("=" * 78)
    print(f"Closure:                 {report['closure_status']}")
    print(
        "Operational accounting: "
        f"{report['operational']['datax_ready']} DATAX_READY + "
        f"{report['operational']['acquisition_job_ready']} "
        "ACQUISITION_JOB_READY + "
        f"{report['operational']['external_blocked']} EXTERNAL_BLOCKER "
        f"= {report['operational']['accounted']}/"
        f"{report['operational']['total']}"
    )
    print(
        "OMC:                     "
        f"{report['omc']['classification']} | "
        f"origin={report['omc']['evidence_origin']} | "
        f"raw={report['omc']['raw_resource_count']} | "
        f"legacy={report['omc']['legacy_projection_records']}"
    )
    print(
        "TRANSTATS:                "
        f"{report['transtats']['classification']}"
    )
    print(
        "External blockers:        "
        + ", ".join(report["external_blockers"])
    )
    print(
        "Legacy records:           "
        f"{report['totals']['legacy_records']}"
    )
    print(
        "Raw resources:            "
        f"{report['totals']['raw_resources']}"
    )

    if report["failed_checks"]:
        print()
        print("FAILED CHECKS:")
        for check in report["failed_checks"]:
            print(f"  - {check}")

    try:
        shown = output_path.relative_to(repo_root)
    except ValueError:
        shown = output_path
    print(f"\nJSON: {shown}")

    return 0 if report["closure_status"] != "OPEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
