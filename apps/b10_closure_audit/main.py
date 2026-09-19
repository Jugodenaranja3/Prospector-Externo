from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AFFECTED_SOURCE_IDS = {
    "anapo",
    "bm",
    "cepal",
    "statistics_denmark",
    "vipfe",
    "cndc",
    "dgac",
    "fifa",
    "icco",
    "mdryt_oap",
    "mdryt",
    "sicoes",
    "mhe",
    "undata",
    "asofin",
    "data_gov",
    "ibce_cao",
    "senamhi",
    "sigma",
    "snis",
    "fdta_valles",
    "bbv",
    "transtats",
}

ALLOWED_EXTERNAL_BLOCKERS = {
    "mhe": {
        "failure_codes": {"ROBOTS_UNREACHABLE"},
        "classification": "EXTERNAL_TLS_CONNECTIVITY",
        "reason": (
            "La estación DATAX no puede validar la cadena TLS del host oficial; "
            "el host alternativo probado también presentó fallo TLS/timeout. "
            "No se desactiva la verificación TLS."
        ),
    },
    "sigma": {
        "failure_codes": {"ROBOTS_UNREACHABLE"},
        "classification": "EXTERNAL_ROBOTS_5XX",
        "reason": (
            "El sitio responde, pero /robots.txt devuelve HTTP 500 en los hosts "
            "probados. No se aplica bypass de robots."
        ),
    },
}

INVENTORY_TOTAL = 52
OPERATIONAL_TOTAL = 41
STATUS_ONLY_TOTAL = 11
BASELINE_AFFECTED = 23
BASELINE_CLEAN_OPERATIONAL = 18


def load_state(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("El state de remediación no es un objeto JSON")
    sources = raw.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("El state no contiene sources como objeto")
    return raw


def evaluate_state(raw: dict[str, Any]) -> dict[str, Any]:
    sources = raw["sources"]
    present = set(sources)

    missing = sorted(AFFECTED_SOURCE_IDS - present)
    extra = sorted(present - AFFECTED_SOURCE_IDS)

    if missing:
        raise ValueError(
            "Faltan fuentes afectadas en el state: " + ", ".join(missing)
        )
    if extra:
        raise ValueError(
            "El state contiene fuentes inesperadas para B10: " + ", ".join(extra)
        )

    status_counter = Counter()
    successful: list[str] = []
    accepted_external_blockers: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for source_id in sorted(AFFECTED_SOURCE_IDS):
        item = sources[source_id]
        if not isinstance(item, dict):
            unresolved.append({
                "source_id": source_id,
                "reason": "STATE_ITEM_INVALID",
            })
            continue

        status = str(item.get("status") or "UNKNOWN")
        status_counter[status] += 1

        summary = item.get("summary")
        if not isinstance(summary, dict):
            summary = {}

        failure_code = summary.get("failure_code")
        workflow = summary.get("workflow")
        resources_found = summary.get("resources_found")
        stop_reason = summary.get("stop_reason")
        attempts = item.get("attempts")

        if status == "SUCCESS":
            successful.append(source_id)
            continue

        blocker_policy = ALLOWED_EXTERNAL_BLOCKERS.get(source_id)
        if (
            status == "FAILED"
            and blocker_policy is not None
            and failure_code in blocker_policy["failure_codes"]
        ):
            accepted_external_blockers.append({
                "source_id": source_id,
                "status": status,
                "failure_code": failure_code,
                "workflow": workflow,
                "resources_found": resources_found,
                "stop_reason": stop_reason,
                "attempts": attempts,
                "classification": blocker_policy["classification"],
                "reason": blocker_policy["reason"],
            })
            continue

        unresolved.append({
            "source_id": source_id,
            "status": status,
            "failure_code": failure_code,
            "workflow": workflow,
            "resources_found": resources_found,
            "stop_reason": stop_reason,
            "attempts": attempts,
        })

    success_count = len(successful)
    blocker_count = len(accepted_external_blockers)

    if success_count + blocker_count + len(unresolved) != BASELINE_AFFECTED:
        raise ValueError("Conteo interno inconsistente en auditoría B10")

    if unresolved:
        closure_status = "OPEN"
    elif blocker_count:
        closure_status = "CLOSED_WITH_EXTERNAL_BLOCKERS"
    else:
        closure_status = "CLOSED_ALL_SUCCESS"

    overall_operational_success = BASELINE_CLEAN_OPERATIONAL + success_count
    overall_operational_blocked = blocker_count
    overall_operational_accounted = (
        overall_operational_success + overall_operational_blocked
    )

    if closure_status != "OPEN" and overall_operational_accounted != OPERATIONAL_TOTAL:
        raise ValueError(
            "La reconciliación operacional no suma 41 fuentes: "
            f"{overall_operational_accounted}"
        )

    return {
        "closure_status": closure_status,
        "inventory": {
            "total_sources": INVENTORY_TOTAL,
            "operational_sources": OPERATIONAL_TOTAL,
            "status_only_sources": STATUS_ONLY_TOTAL,
        },
        "baseline": {
            "affected_sources": BASELINE_AFFECTED,
            "clean_operational_sources": BASELINE_CLEAN_OPERATIONAL,
        },
        "remediation": {
            "success_count": success_count,
            "accepted_external_blocker_count": blocker_count,
            "unresolved_count": len(unresolved),
            "status_counts": dict(sorted(status_counter.items())),
            "successful_source_ids": successful,
            "accepted_external_blockers": accepted_external_blockers,
            "unresolved": unresolved,
        },
        "reconciled_operational": {
            "successful": overall_operational_success,
            "external_blocked": overall_operational_blocked,
            "accounted": overall_operational_accounted,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditoría final de cierre B10 sin red."
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(".runtime/b10_remediation/state.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".runtime/b10_closure/latest.json"),
    )
    args = parser.parse_args()

    raw = load_state(args.state)
    report = evaluate_state(raw)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print("B10 — FINAL CLOSURE AUDIT")
    print("=" * 78)
    print(f"Closure:                  {report['closure_status']}")
    print(
        "Affected remediation:     "
        f"{report['remediation']['success_count']} SUCCESS / "
        f"{report['remediation']['accepted_external_blocker_count']} "
        "EXTERNAL_BLOCKER / "
        f"{report['remediation']['unresolved_count']} UNRESOLVED"
    )
    print(
        "Operational reconciliation:"
        f" {report['reconciled_operational']['successful']} SUCCESS / "
        f"{report['reconciled_operational']['external_blocked']} EXTERNAL_BLOCKED "
        f"/ {report['reconciled_operational']['accounted']} ACCOUNTED"
    )
    print(
        "Full inventory:            "
        f"{report['inventory']['operational_sources']} operational + "
        f"{report['inventory']['status_only_sources']} status-only = "
        f"{report['inventory']['total_sources']}"
    )

    blockers = report["remediation"]["accepted_external_blockers"]
    if blockers:
        print("\nAccepted external blockers:")
        for blocker in blockers:
            print(
                f"  - {blocker['source_id']}: "
                f"{blocker['classification']} "
                f"({blocker['failure_code']})"
            )

    unresolved = report["remediation"]["unresolved"]
    if unresolved:
        print("\nUNRESOLVED:")
        for item in unresolved:
            print(
                f"  - {item['source_id']}: "
                f"{item.get('status')} / {item.get('failure_code')}"
            )

    print(f"\nJSON: {args.output}")

    return 0 if report["closure_status"] != "OPEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
