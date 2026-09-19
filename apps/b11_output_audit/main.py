from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


OPERATIONAL_SOURCE_IDS = (
    "anapo",
    "aps",
    "asfi_valores",
    "bcb",
    "bm",
    "cepal",
    "finrural",
    "mmym",
    "omc",
    "statistics_denmark",
    "vipfe",
    "ada",
    "cadexco",
    "cndc",
    "dgac",
    "fifa",
    "icco",
    "itu",
    "mdryt_oap",
    "sicoes",
    "aps_soat",
    "asfi",
    "asfi_finrural",
    "fdta_valles",
    "ine",
    "mhe",
    "seprec",
    "undata",
    "ae",
    "asfi_bcb",
    "asofin",
    "att",
    "bbv",
    "data_gov",
    "ibce_cao",
    "mdryt",
    "min_educacion",
    "senamhi",
    "sigma",
    "snis",
    "transtats",
)

EXTERNAL_BLOCKERS = {
    "mhe": "EXTERNAL_TLS_CONNECTIVITY",
    "sigma": "EXTERNAL_ROBOTS_5XX",
}

LEGACY_REQUIRED_FIELDS = (
    "descripcion",
    "url_descarga",
    "fecha_actualizacion",
    "tipo_archivo",
    "url_origen",
    "metodo_deteccion",
)

HIGH_PRIORITY_EXTENSIONS = {
    ".csv",
    ".tsv",
    ".xls",
    ".xlsx",
    ".ods",
    ".json",
    ".xml",
    ".geojson",
    ".zip",
    ".tar",
    ".gz",
    ".parquet",
}

MEDIUM_PRIORITY_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
}

BASELINE_ROOT_REL = Path("output") / "b10-final" / "b10-final"
REMEDIATION_ROOT_REL = Path("output") / "b10-remediation"
B11_REFRESH_ROOT_REL = Path("output") / "b11-refresh"
REMEDIATION_STATE_REL = Path(".runtime") / "b10_remediation" / "state.json"
CLOSURE_REPORT_REL = Path(".runtime") / "b10_closure" / "latest.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_load_json(path: Path) -> Any | None:
    try:
        return load_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def latest_json(paths: Iterable[Path]) -> Path | None:
    candidates = [path for path in paths if path.is_file()]
    if not candidates:
        return None

    # Run IDs embed timestamps, so filename ordering is deterministic enough;
    # mtime is used only as a stable tiebreaker.
    return max(
        candidates,
        key=lambda path: (path.name, path.stat().st_mtime_ns),
    )


def load_remediation_state(repo_root: Path) -> dict[str, Any]:
    path = repo_root / REMEDIATION_STATE_REL
    if not path.exists():
        return {}
    raw = safe_load_json(path)
    if not isinstance(raw, dict):
        return {}
    sources = raw.get("sources")
    return sources if isinstance(sources, dict) else {}


def choose_evidence_root(
    repo_root: Path,
    source_id: str,
    remediation_state: dict[str, Any],
) -> tuple[Path, str]:
    refresh = repo_root / B11_REFRESH_ROOT_REL / source_id
    if (refresh / "reports").exists():
        return refresh, "b11_refresh"

    if source_id in remediation_state:
        return repo_root / REMEDIATION_ROOT_REL / source_id, "remediation"

    return repo_root / BASELINE_ROOT_REL / source_id, "baseline"


def find_report(evidence_root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    report_path = latest_json((evidence_root / "reports").glob("*.json"))
    if report_path is None:
        return None, None

    raw = safe_load_json(report_path)
    return report_path, raw if isinstance(raw, dict) else None


def extract_source_result(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}

    rows = report.get("source_results")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                return row

    # B10 helper summaries can also be flat.
    if any(
        key in report
        for key in (
            "execution_status",
            "content_status",
            "workflow",
            "failure_code",
        )
    ):
        return report

    return {}


def find_snapshot(
    evidence_root: Path,
    run_id: str | None,
) -> tuple[Path | None, dict[str, Any] | None]:
    snapshots_dir = evidence_root / "state" / "snapshots"
    if not snapshots_dir.exists():
        return None, None

    candidates = list(snapshots_dir.glob("*.json"))
    if run_id:
        matching = [
            path for path in candidates
            if run_id in path.name
        ]
        if matching:
            candidates = matching

    snapshot_path = latest_json(candidates)
    if snapshot_path is None:
        return None, None

    raw = safe_load_json(snapshot_path)
    return snapshot_path, raw if isinstance(raw, dict) else None


def find_map_files(evidence_root: Path) -> list[Path]:
    result: list[Path] = []
    for path in evidence_root.rglob("mapa_*.json"):
        name = path.name.lower()
        if name.endswith("_compact.json") or name.endswith("_tree.json"):
            continue
        result.append(path)
    return sorted(result)


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def legacy_records(document: Any) -> tuple[int, int]:
    if not isinstance(document, dict) or "ESTADISTICAS" not in document:
        return 0, 0

    candidate_count = 0
    invalid_count = 0

    for row in walk_dicts(document["ESTADISTICAS"]):
        keys = set(row)
        if not keys.intersection(LEGACY_REQUIRED_FIELDS):
            continue

        candidate_count += 1
        if not set(LEGACY_REQUIRED_FIELDS).issubset(keys):
            invalid_count += 1

    return candidate_count, invalid_count


def find_legacy_projection_files(
    evidence_root: Path,
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    if not evidence_root.exists():
        return found

    for path in evidence_root.rglob("*.json"):
        raw = safe_load_json(path)
        if not isinstance(raw, dict) or "ESTADISTICAS" not in raw:
            continue

        records, invalid = legacy_records(raw)
        found.append(
            {
                "path": str(path),
                "records": records,
                "invalid_records": invalid,
            }
        )

    return sorted(found, key=lambda item: item["path"])


def normalize_extension(resource: dict[str, Any]) -> str:
    extension = str(resource.get("file_extension") or "").strip().lower()
    if extension and not extension.startswith("."):
        extension = "." + extension

    if extension:
        return extension

    url = str(resource.get("url") or "")
    path_part = url.split("?", 1)[0].split("#", 1)[0]
    filename = path_part.rsplit("/", 1)[-1]
    if "." in filename:
        suffix = "." + filename.rsplit(".", 1)[-1].lower()
        if 1 < len(suffix) <= 12:
            return suffix

    return ""


def resource_statistics(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {
            "snapshot_total": None,
            "resources_len": None,
            "resource_types": {},
            "extensions": {},
            "api_formats": {},
            "high_priority": 0,
            "medium_priority": 0,
            "sample_urls": [],
        }

    resources = snapshot.get("resources")
    if not isinstance(resources, list):
        resources = []

    type_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    api_formats: Counter[str] = Counter()
    high_priority = 0
    medium_priority = 0
    sample_urls: list[str] = []

    for raw_resource in resources:
        if not isinstance(raw_resource, dict):
            continue

        resource_type = str(
            raw_resource.get("resource_type") or "file"
        ).lower()
        type_counts[resource_type] += 1

        extension = normalize_extension(raw_resource)
        if extension:
            extension_counts[extension] += 1

        api = raw_resource.get("api")
        api_format = None
        if isinstance(api, dict):
            api_format = str(api.get("format") or "").lower()
            if api_format:
                api_formats[api_format] += 1

        content_type = str(
            raw_resource.get("content_type") or ""
        ).lower()

        is_high = (
            resource_type == "api"
            or extension in HIGH_PRIORITY_EXTENSIONS
            or any(
                token in content_type
                for token in (
                    "json",
                    "xml",
                    "csv",
                    "tab-separated",
                    "geo+json",
                )
            )
            or api_format in {
                "json",
                "xml",
                "csv",
                "tsv",
                "geojson",
                "ndjson",
            }
        )

        if is_high:
            high_priority += 1
        elif extension in MEDIUM_PRIORITY_EXTENSIONS:
            medium_priority += 1

        url = raw_resource.get("url")
        if isinstance(url, str) and url and len(sample_urls) < 3:
            sample_urls.append(url)

    return {
        "snapshot_total": snapshot.get("total_resources"),
        "resources_len": len(resources),
        "resource_types": dict(sorted(type_counts.items())),
        "extensions": dict(
            sorted(
                extension_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "api_formats": dict(sorted(api_formats.items())),
        "high_priority": high_priority,
        "medium_priority": medium_priority,
        "sample_urls": sample_urls,
    }


def find_special_downstream_manifest(
    evidence_root: Path,
) -> tuple[Path | None, str | None]:
    candidates = sorted(
        evidence_root.rglob("acquisition_job.json")
    )
    for path in candidates:
        raw = safe_load_json(path)
        if not isinstance(raw, dict):
            continue
        decision = raw.get("decision")
        if decision == "ACQUISITION_JOB_READY":
            return path, decision
    return None, None


def classify_source(
    *,
    source_id: str,
    report_present: bool,
    execution_status: str | None,
    raw_resource_count: int | None,
    legacy_record_count: int,
    special_decision: str | None = None,
) -> str:
    if source_id in EXTERNAL_BLOCKERS and execution_status == "FAILED":
        return "EXTERNAL_BLOCKER"

    if not report_present:
        return "EVIDENCE_MISSING"

    if execution_status != "SUCCESS":
        return "EXECUTION_NOT_SUCCESS"

    if special_decision == "ACQUISITION_JOB_READY":
        return "ACQUISITION_JOB_READY"

    if not raw_resource_count:
        return "SUCCESS_EMPTY"

    if legacy_record_count > 0:
        return "DATAX_READY"

    return "RAW_READY_NO_PROJECTION"


def audit_source(
    repo_root: Path,
    source_id: str,
    remediation_state: dict[str, Any],
) -> dict[str, Any]:
    evidence_root, evidence_origin = choose_evidence_root(
        repo_root,
        source_id,
        remediation_state,
    )

    report_path, report = find_report(evidence_root)
    source_result = extract_source_result(report)

    run_id = (
        source_result.get("run_id")
        or (report or {}).get("run_id")
    )
    run_id = str(run_id) if run_id else None

    snapshot_path, snapshot = find_snapshot(
        evidence_root,
        run_id,
    )
    stats = resource_statistics(snapshot)

    coverage = source_result.get("coverage")
    if not isinstance(coverage, dict):
        coverage = {}

    coverage_resources = coverage.get("resources_found")
    snapshot_total = stats["snapshot_total"]
    resources_len = stats["resources_len"]

    raw_resource_count: int | None
    if isinstance(snapshot_total, int):
        raw_resource_count = snapshot_total
    elif isinstance(resources_len, int):
        raw_resource_count = resources_len
    elif isinstance(coverage_resources, int):
        raw_resource_count = coverage_resources
    else:
        raw_resource_count = None

    projection_files = find_legacy_projection_files(evidence_root)
    projection_record_count = sum(
        int(item["records"])
        for item in projection_files
    )
    projection_invalid_count = sum(
        int(item["invalid_records"])
        for item in projection_files
    )

    special_manifest_path, special_decision = (
        find_special_downstream_manifest(evidence_root)
    )

    execution_status = source_result.get("execution_status")
    execution_status = (
        str(execution_status)
        if execution_status is not None
        else None
    )

    classification = classify_source(
        source_id=source_id,
        report_present=report_path is not None,
        execution_status=execution_status,
        raw_resource_count=raw_resource_count,
        legacy_record_count=projection_record_count,
        special_decision=special_decision,
    )

    issues: list[str] = []

    if not evidence_root.exists():
        issues.append("EVIDENCE_ROOT_MISSING")
    if report_path is None:
        issues.append("REPORT_MISSING")

    if (
        isinstance(snapshot_total, int)
        and isinstance(resources_len, int)
        and snapshot_total != resources_len
    ):
        issues.append("SNAPSHOT_TOTAL_MISMATCH")

    if (
        isinstance(snapshot_total, int)
        and isinstance(coverage_resources, int)
        and snapshot_total != coverage_resources
        and execution_status == "SUCCESS"
    ):
        issues.append("REPORT_SNAPSHOT_RESOURCE_MISMATCH")

    if projection_invalid_count:
        issues.append("LEGACY_PROJECTION_SCHEMA_INCOMPLETE")

    if raw_resource_count and snapshot_path is None:
        issues.append("RESOURCES_REPORTED_WITHOUT_SNAPSHOT")

    map_files = find_map_files(evidence_root)
    if execution_status == "SUCCESS" and snapshot_path is not None and not map_files:
        issues.append("MAP_ARTIFACT_MISSING")

    return {
        "logical_source_id": source_id,
        "evidence_origin": evidence_origin,
        "evidence_root": str(evidence_root),
        "report_path": str(report_path) if report_path else None,
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "run_id": run_id,
        "physical_source_id": source_result.get("source_id"),
        "workflow": source_result.get("workflow"),
        "execution_status": execution_status,
        "content_status": source_result.get("content_status"),
        "failure_code": source_result.get("failure_code"),
        "stop_reason": coverage.get("stop_reason"),
        "pages_visited": coverage.get("pages_visited"),
        "coverage_resources_found": coverage_resources,
        "raw_resource_count": raw_resource_count,
        "snapshot_total_resources": snapshot_total,
        "snapshot_resources_len": resources_len,
        "resource_types": stats["resource_types"],
        "extensions": stats["extensions"],
        "api_formats": stats["api_formats"],
        "high_priority_resources": stats["high_priority"],
        "medium_priority_resources": stats["medium_priority"],
        "sample_urls": stats["sample_urls"],
        "map_files": [str(path) for path in map_files],
        "legacy_projection_files": projection_files,
        "legacy_projection_records": projection_record_count,
        "legacy_projection_invalid_records": projection_invalid_count,
        "special_downstream_manifest": (
            str(special_manifest_path)
            if special_manifest_path is not None
            else None
        ),
        "special_downstream_decision": special_decision,
        "classification": classification,
        "external_blocker_classification": EXTERNAL_BLOCKERS.get(source_id),
        "issues": issues,
    }


def build_audit(repo_root: Path) -> dict[str, Any]:
    remediation_state = load_remediation_state(repo_root)

    rows = [
        audit_source(
            repo_root=repo_root,
            source_id=source_id,
            remediation_state=remediation_state,
        )
        for source_id in OPERATIONAL_SOURCE_IDS
    ]

    classification_counts = Counter(
        row["classification"]
        for row in rows
    )

    execution_counts = Counter(
        row["execution_status"] or "UNKNOWN"
        for row in rows
    )

    sources_with_resources = [
        row
        for row in rows
        if isinstance(row["raw_resource_count"], int)
        and row["raw_resource_count"] > 0
    ]

    total_raw_resources = sum(
        int(row["raw_resource_count"])
        for row in sources_with_resources
    )

    total_high_priority = sum(
        int(row["high_priority_resources"])
        for row in rows
    )

    total_medium_priority = sum(
        int(row["medium_priority_resources"])
        for row in rows
    )

    total_legacy_records = sum(
        int(row["legacy_projection_records"])
        for row in rows
    )

    issue_sources = [
        row for row in rows
        if row["issues"]
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "operational_sources": len(OPERATIONAL_SOURCE_IDS),
            "baseline_root": str(repo_root / BASELINE_ROOT_REL),
            "remediation_root": str(repo_root / REMEDIATION_ROOT_REL),
            "remediation_sources_present": len(remediation_state),
        },
        "summary": {
            "classification_counts": dict(
                sorted(classification_counts.items())
            ),
            "execution_counts": dict(
                sorted(execution_counts.items())
            ),
            "sources_with_raw_resources": len(sources_with_resources),
            "sources_without_raw_resources": (
                len(OPERATIONAL_SOURCE_IDS) - len(sources_with_resources)
            ),
            "total_raw_resources": total_raw_resources,
            "total_high_priority_resources": total_high_priority,
            "total_medium_priority_resources": total_medium_priority,
            "sources_with_legacy_projection": sum(
                1
                for row in rows
                if row["legacy_projection_records"] > 0
            ),
            "total_legacy_projection_records": total_legacy_records,
            "sources_with_structural_issues": len(issue_sources),
        },
        "sources": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    counts = summary["classification_counts"]

    lines = [
        "# B11 — Auditoría offline de outputs consumibles",
        "",
        f"Generado: `{report['generated_at']}`",
        "",
        "## Resumen",
        "",
        f"- Fuentes operacionales auditadas: **{report['scope']['operational_sources']}**",
        f"- Fuentes con recursos raw: **{summary['sources_with_raw_resources']}**",
        f"- Recursos raw totales: **{summary['total_raw_resources']}**",
        f"- Recursos de prioridad alta DATAX: **{summary['total_high_priority_resources']}**",
        f"- Recursos de prioridad media: **{summary['total_medium_priority_resources']}**",
        f"- Fuentes con proyección legacy detectada: **{summary['sources_with_legacy_projection']}**",
        f"- Registros legacy detectados: **{summary['total_legacy_projection_records']}**",
        "",
        "## Clasificación",
        "",
    ]

    for key, value in sorted(counts.items()):
        lines.append(f"- `{key}`: **{value}**")

    lines.extend(
        [
            "",
            "## Detalle por fuente",
            "",
            "| Fuente | Clase | Ejecución | Raw | Alta | Media | Legacy | Workflow | Issues |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )

    for row in report["sources"]:
        issues = ", ".join(row["issues"]) if row["issues"] else "-"
        raw = (
            str(row["raw_resource_count"])
            if row["raw_resource_count"] is not None
            else "-"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    row["logical_source_id"],
                    row["classification"],
                    row["execution_status"] or "-",
                    raw,
                    str(row["high_priority_resources"]),
                    str(row["medium_priority_resources"]),
                    str(row["legacy_projection_records"]),
                    str(row["workflow"] or "-"),
                    issues,
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Interpretación",
            "",
            "- `DATAX_READY`: hay recursos raw y al menos una proyección legacy detectada.",
            "- `ACQUISITION_JOB_READY`: la evidencia representa un trabajo público de adquisición, no un archivo materializado; se conserva en un manifest especializado y no se falsea dentro de ESTADISTICAS.",
            "- `RAW_READY_NO_PROJECTION`: el Prospector encontró recursos, pero no existe todavía una salida legacy ni una decisión downstream especializada.",
            "- `SUCCESS_EMPTY`: la ejecución terminó correctamente pero no produjo recursos en su evidencia más reciente.",
            "- `EXTERNAL_BLOCKER`: bloqueo externo aceptado durante B10; no se falsea como éxito.",
            "- `EVIDENCE_MISSING` / `EXECUTION_NOT_SUCCESS`: requiere revisión estructural antes de proyección.",
            "",
            "B11 no hace HTTP, no repite crawls y no modifica outputs de B10.",
            "",
        ]
    )

    return "\n".join(lines)


def print_console(report: dict[str, Any]) -> None:
    summary = report["summary"]
    counts = summary["classification_counts"]

    print("=" * 78)
    print("B11 — OUTPUT CONSUMABILITY AUDIT")
    print("=" * 78)
    print(f"Operational sources:       {report['scope']['operational_sources']}")
    print(f"Sources with raw resources:{summary['sources_with_raw_resources']:>8}")
    print(f"Total raw resources:       {summary['total_raw_resources']:>8}")
    print(f"High-priority resources:   {summary['total_high_priority_resources']:>8}")
    print(f"Medium-priority resources: {summary['total_medium_priority_resources']:>8}")
    print(f"Legacy-projected sources:  {summary['sources_with_legacy_projection']:>8}")
    print(f"Legacy records:            {summary['total_legacy_projection_records']:>8}")
    print()
    print("Classification:")
    for key in (
        "DATAX_READY",
        "ACQUISITION_JOB_READY",
        "RAW_READY_NO_PROJECTION",
        "SUCCESS_EMPTY",
        "EXTERNAL_BLOCKER",
        "EXECUTION_NOT_SUCCESS",
        "EVIDENCE_MISSING",
    ):
        print(f"  {key:<25} {counts.get(key, 0)}")

    raw_no_projection = [
        row["logical_source_id"]
        for row in report["sources"]
        if row["classification"] == "RAW_READY_NO_PROJECTION"
    ]
    empty = [
        row["logical_source_id"]
        for row in report["sources"]
        if row["classification"] == "SUCCESS_EMPTY"
    ]
    blocked = [
        row["logical_source_id"]
        for row in report["sources"]
        if row["classification"] == "EXTERNAL_BLOCKER"
    ]
    problematic = [
        row["logical_source_id"]
        for row in report["sources"]
        if row["classification"] in {
            "EXECUTION_NOT_SUCCESS",
            "EVIDENCE_MISSING",
        }
    ]

    top = sorted(
        (
            row
            for row in report["sources"]
            if isinstance(row["raw_resource_count"], int)
            and row["raw_resource_count"] > 0
        ),
        key=lambda row: (
            -int(row["raw_resource_count"]),
            row["logical_source_id"],
        ),
    )[:10]

    print()
    print("Top raw sources:")
    if not top:
        print("  (none)")
    else:
        for row in top:
            print(
                f"  {row['logical_source_id']:<22} "
                f"raw={row['raw_resource_count']:<5} "
                f"high={row['high_priority_resources']:<5} "
                f"legacy={row['legacy_projection_records']}"
            )

    print()
    print("RAW_READY_NO_PROJECTION:")
    print("  " + (", ".join(raw_no_projection) if raw_no_projection else "(none)"))
    print("SUCCESS_EMPTY:")
    print("  " + (", ".join(empty) if empty else "(none)"))
    print("EXTERNAL_BLOCKER:")
    print("  " + (", ".join(blocked) if blocked else "(none)"))
    print("STRUCTURAL/EXECUTION REVIEW:")
    print("  " + (", ".join(problematic) if problematic else "(none)"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B11: auditoría offline de outputs B10 y readiness DATAX."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path(".runtime/b11_output_audit/latest.json"),
    )
    parser.add_argument(
        "--md-output",
        type=Path,
        default=Path(".runtime/b11_output_audit/latest.md"),
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    report = build_audit(repo_root)

    json_output = (
        args.json_output
        if args.json_output.is_absolute()
        else repo_root / args.json_output
    )
    md_output = (
        args.md_output
        if args.md_output.is_absolute()
        else repo_root / args.md_output
    )

    json_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)

    json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_output.write_text(
        render_markdown(report),
        encoding="utf-8",
    )

    print_console(report)
    print()
    print(f"JSON: {json_output.relative_to(repo_root)}")
    print(f"MD:   {md_output.relative_to(repo_root)}")

    # B11A is an inventory/audit. Findings such as SUCCESS_EMPTY are data for
    # the next implementation batch, not a reason to make the audit itself fail.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
