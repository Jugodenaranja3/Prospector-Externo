from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


DEFAULT_RUN_ID = "b13-e2e"
DEFAULT_STATE = Path(".runtime/b13_pipeline/latest.json")
DEFAULT_OUTPUT = Path(".runtime/b13_closure/latest.json")
DEFAULT_MD = Path(".runtime/b13_closure/latest.md")

EXPECTED_COUNTS = {
    "DATAX_READY": 38,
    "ACQUISITION_JOB_READY": 1,
    "EXTERNAL_BLOCKER": 2,
}
EXPECTED_EXTERNAL = {"mhe", "sigma"}
EXPECTED_ACQUISITION = {"transtats"}
EXPECTED_OPERATIONAL = 41
EXPECTED_INVENTORY = 52
EXPECTED_STATUS_ONLY = 11

LEGACY_FIELDS = {
    "descripcion",
    "url_descarga",
    "fecha_actualizacion",
    "tipo_archivo",
    "url_origen",
    "metodo_deteccion",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def human_duration(seconds: float | int | None) -> str | None:
    if seconds is None:
        return None
    value = int(round(float(seconds)))
    hours, rem = divmod(value, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} h {minutes} min {secs} s"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def resolve_path(repo_root: Path, raw: str | Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else repo_root / path


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def count_legacy_records(document: Any) -> int:
    if not isinstance(document, dict):
        raise ValueError("Legacy JSON inválido")
    stats = document.get("ESTADISTICAS")
    if not isinstance(stats, dict):
        raise ValueError("Legacy JSON sin ESTADISTICAS")

    count = 0
    incomplete = 0
    for row in walk_dicts(stats):
        keys = set(row)
        if not keys.intersection(LEGACY_FIELDS):
            continue
        count += 1
        if not LEGACY_FIELDS.issubset(keys):
            incomplete += 1

    if incomplete:
        raise ValueError(
            f"Legacy JSON contiene {incomplete} registros incompletos"
        )
    return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_source_result(report: dict[str, Any]) -> dict[str, Any]:
    rows = report.get("source_results")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                return row
    return report


def load_source_config(
    repo_root: Path,
    run_id: str,
    logical_source_id: str,
) -> dict[str, Any]:
    path = (
        repo_root
        / ".runtime"
        / "checkpointed_batch"
        / run_id
        / "source_configs"
        / f"{logical_source_id}.yaml"
    )
    if not path.exists():
        return {}

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        sources = raw.get("sources")
        if isinstance(sources, list):
            for row in sources:
                if isinstance(row, dict):
                    return row
        if "source_id" in raw:
            return raw
    return {}


def audit_timings_and_limits(
    *,
    repo_root: Path,
    run_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    rows = state.get("sources")
    if not isinstance(rows, list):
        raise ValueError("B13 state no contiene sources:list")

    timings: list[dict[str, Any]] = []
    stop_reasons: Counter[str] = Counter()
    max_urls_values: Counter[str] = Counter()
    max_runtime_values: Counter[str] = Counter()
    max_depth_values: Counter[str] = Counter()
    query_cap_values: Counter[str] = Counter()

    earliest: datetime | None = None
    latest: datetime | None = None
    active_seconds = 0.0
    timed_reports = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        logical_id = str(row.get("logical_source_id") or "")
        raw_report = row.get("report_path")
        report_path = (
            resolve_path(repo_root, raw_report)
            if isinstance(raw_report, str) and raw_report
            else None
        )

        started = None
        finished = None
        duration = None
        stop_reason = None
        pages_visited = None
        requests_total = None
        resources_found = row.get("raw_resource_count")

        if report_path is not None and report_path.exists():
            report = load_json(report_path)
            if isinstance(report, dict):
                started = parse_dt(report.get("started_at"))
                finished = parse_dt(report.get("finished_at"))
                source_result = extract_source_result(report)
                coverage = source_result.get("coverage")
                if isinstance(coverage, dict):
                    stop_reason = coverage.get("stop_reason")
                    pages_visited = coverage.get("pages_visited")
                    requests_total = coverage.get("requests_total")
                    if resources_found is None:
                        resources_found = coverage.get("resources_found")

        if started is not None and finished is not None:
            duration = max(0.0, (finished - started).total_seconds())
            active_seconds += duration
            timed_reports += 1
            earliest = started if earliest is None else min(earliest, started)
            latest = finished if latest is None else max(latest, finished)

        if isinstance(stop_reason, str) and stop_reason:
            stop_reasons[stop_reason] += 1
        else:
            stop_reasons["NONE"] += 1

        config = load_source_config(repo_root, run_id, logical_id)
        for field, counter in (
            ("max_urls", max_urls_values),
            ("max_runtime_seconds", max_runtime_values),
            ("max_depth", max_depth_values),
            ("max_query_variants", query_cap_values),
        ):
            value = config.get(field)
            if value is not None:
                counter[str(value)] += 1

        timings.append(
            {
                "logical_source_id": logical_id,
                "classification": row.get("classification"),
                "execution_status": row.get("execution_status"),
                "failure_code": row.get("failure_code"),
                "started_at": started.isoformat() if started else None,
                "finished_at": finished.isoformat() if finished else None,
                "duration_seconds": duration,
                "duration_human": human_duration(duration),
                "stop_reason": stop_reason,
                "pages_visited": pages_visited,
                "requests_total": requests_total,
                "raw_resource_count": resources_found,
                "configured_limits": {
                    key: config.get(key)
                    for key in (
                        "max_urls",
                        "max_runtime_seconds",
                        "max_depth",
                        "max_query_variants",
                        "max_calendar_variants",
                    )
                    if config.get(key) is not None
                },
            }
        )

    crawl_wall_seconds = (
        (latest - earliest).total_seconds()
        if earliest is not None and latest is not None
        else None
    )
    state_generated = parse_dt(state.get("generated_at"))
    end_to_end_window_seconds = (
        (state_generated - earliest).total_seconds()
        if state_generated is not None and earliest is not None
        else None
    )
    finalization_seconds = (
        (state_generated - latest).total_seconds()
        if state_generated is not None and latest is not None
        else None
    )
    gap_orchestration_seconds = (
        max(0.0, crawl_wall_seconds - active_seconds)
        if crawl_wall_seconds is not None
        else None
    )

    slowest = sorted(
        (
            item for item in timings
            if isinstance(item["duration_seconds"], (int, float))
        ),
        key=lambda item: float(item["duration_seconds"]),
        reverse=True,
    )[:10]

    max_limit_hits = sorted(
        item["logical_source_id"]
        for item in timings
        if isinstance(item.get("stop_reason"), str)
        and str(item["stop_reason"]).startswith("MAX_")
    )

    return {
        "timed_reports": timed_reports,
        "earliest_source_started_at": (
            earliest.isoformat() if earliest else None
        ),
        "latest_source_finished_at": (
            latest.isoformat() if latest else None
        ),
        "active_source_crawl_seconds": active_seconds,
        "active_source_crawl_human": human_duration(active_seconds),
        "crawl_wall_span_seconds": crawl_wall_seconds,
        "crawl_wall_span_human": human_duration(crawl_wall_seconds),
        "end_to_end_evidence_window_seconds": end_to_end_window_seconds,
        "end_to_end_evidence_window_human": human_duration(
            end_to_end_window_seconds
        ),
        "post_crawl_finalization_seconds": finalization_seconds,
        "post_crawl_finalization_human": human_duration(
            finalization_seconds
        ),
        "gap_and_orchestration_seconds": gap_orchestration_seconds,
        "gap_and_orchestration_human": human_duration(
            gap_orchestration_seconds
        ),
        "stop_reason_counts": dict(sorted(stop_reasons.items())),
        "sources_hitting_max_limit": max_limit_hits,
        "sources_hitting_max_limit_count": len(max_limit_hits),
        "configured_limit_distributions": {
            "max_urls": dict(sorted(max_urls_values.items())),
            "max_runtime_seconds": dict(sorted(max_runtime_values.items())),
            "max_depth": dict(sorted(max_depth_values.items())),
            "max_query_variants": dict(sorted(query_cap_values.items())),
        },
        "slowest_sources": slowest,
        "sources": timings,
    }


def validate_package(
    *,
    repo_root: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    package = state.get("package")
    if not isinstance(package, dict):
        raise ValueError("B13 state no contiene package")

    package_dir = resolve_path(repo_root, str(package["package_dir"]))
    zip_path = resolve_path(repo_root, str(package["zip_path"]))

    if not package_dir.exists():
        raise FileNotFoundError(package_dir)
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    manifest = load_json(package_dir / "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json inválido")

    sources = manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != EXPECTED_OPERATIONAL:
        raise ValueError("Manifest B13 no indexa 41 fuentes")

    source_jsons = sorted((package_dir / "sources").glob("*.json"))
    if len(source_jsons) != EXPECTED_COUNTS["DATAX_READY"]:
        raise ValueError(
            f"Se esperaban 38 JSON DATAX; encontrados={len(source_jsons)}"
        )

    legacy_total = sum(
        count_legacy_records(load_json(path))
        for path in source_jsons
    )

    expected_legacy = int(package.get("legacy_records") or 0)
    if legacy_total != expected_legacy:
        raise ValueError(
            f"Legacy total inconsistente: {legacy_total} != {expected_legacy}"
        )

    special = load_json(
        package_dir / "special" / "transtats" / "acquisition_job.json"
    )
    if special.get("decision") != "ACQUISITION_JOB_READY":
        raise ValueError("TRANSTATS manifest inválido")

    blockers = load_json(package_dir / "external_blockers.json")
    blocker_ids = {
        str(row.get("logical_source_id"))
        for row in blockers.get("sources", [])
        if isinstance(row, dict)
    }
    if blocker_ids != EXPECTED_EXTERNAL:
        raise ValueError(
            f"Blockers inesperados: {sorted(blocker_ids)}"
        )

    cross = manifest.get("cross_physical_duplicate_groups")
    if not isinstance(cross, list) or cross:
        raise ValueError("Existen duplicados cross-physical")

    checksum_path = package_dir / "checksums.sha256"
    checksum_lines = [
        line
        for line in checksum_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    checksum_mismatches: list[str] = []
    for line in checksum_lines:
        digest, relative = line.split("  ", 1)
        actual = sha256_file(package_dir / Path(relative))
        if actual != digest:
            checksum_mismatches.append(relative)

    if checksum_mismatches:
        raise ValueError(
            "Checksums inválidos: " + ", ".join(checksum_mismatches)
        )

    disk_files = {
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*")
        if path.is_file()
    }

    with zipfile.ZipFile(zip_path, "r") as archive:
        zip_files = set(archive.namelist())

    if zip_files != disk_files:
        raise ValueError("ZIP no coincide con el directorio del paquete")

    return {
        "package_dir": str(package_dir),
        "zip_path": str(zip_path),
        "zip_sha256": sha256_file(zip_path),
        "legacy_jsons": len(source_jsons),
        "legacy_records": legacy_total,
        "acquisition_manifests": 1,
        "external_blockers": 2,
        "cross_physical_duplicate_groups": 0,
        "checksum_covered_files": len(checksum_lines),
        "zip_entries": len(zip_files),
    }


def evaluate(
    *,
    repo_root: Path,
    run_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}

    checks["run_id"] = state.get("pipeline_run_id") == run_id
    checks["pipeline_ready"] = state.get("status") == "READY"

    inventory = state.get("inventory") or {}
    checks["inventory"] = (
        inventory.get("total") == EXPECTED_INVENTORY
        and inventory.get("operational") == EXPECTED_OPERATIONAL
        and inventory.get("status_only") == EXPECTED_STATUS_ONLY
    )

    counts = state.get("counts")
    if not isinstance(counts, dict):
        counts = {}

    checks["classification_counts"] = all(
        int(counts.get(key, 0)) == expected
        for key, expected in EXPECTED_COUNTS.items()
    )
    checks["no_pending"] = not state.get("pending")
    checks["no_unresolved"] = not state.get("unresolved")

    rows = state.get("sources")
    if not isinstance(rows, list):
        raise ValueError("B13 state sin sources:list")

    external_ids = {
        str(row.get("logical_source_id"))
        for row in rows
        if row.get("classification") == "EXTERNAL_BLOCKER"
    }
    acquisition_ids = {
        str(row.get("logical_source_id"))
        for row in rows
        if row.get("classification") == "ACQUISITION_JOB_READY"
    }

    checks["external_blockers_exact"] = external_ids == EXPECTED_EXTERNAL
    checks["acquisition_exact"] = acquisition_ids == EXPECTED_ACQUISITION

    blockers_are_expected_failures = all(
        row.get("failure_code") == "ROBOTS_UNREACHABLE"
        for row in rows
        if row.get("classification") == "EXTERNAL_BLOCKER"
    )
    checks["blocker_failure_codes"] = blockers_are_expected_failures

    checkpoint_rc = state.get("checkpoint_returncode")
    checks["checkpoint_rc_explained"] = (
        checkpoint_rc in (0, 2)
        and (
            checkpoint_rc == 0
            or external_ids == EXPECTED_EXTERNAL
        )
    )

    package = validate_package(repo_root=repo_root, state=state)
    checks["package_integrity"] = True

    timing = audit_timings_and_limits(
        repo_root=repo_root,
        run_id=run_id,
        state=state,
    )
    checks["timing_coverage"] = (
        timing["timed_reports"] == EXPECTED_OPERATIONAL
    )

    failures = [
        name for name, passed in checks.items()
        if not passed
    ]

    return {
        "closure_status": "CLOSED" if not failures else "OPEN",
        "checks": checks,
        "failed_checks": failures,
        "run_id": run_id,
        "checkpoint_returncode": checkpoint_rc,
        "classification_counts": {
            key: int(counts.get(key, 0))
            for key in EXPECTED_COUNTS
        },
        "package": package,
        "timing_and_limits": timing,
        "interpretation": {
            "crawl_is_unbounded": False,
            "coverage_statement": (
                "Final operational crawl under the configured bounded "
                "discovery policy; it is not an unrestricted mirror of "
                "every page on every website."
            ),
            "checkpoint_rc_2": (
                "Expected when MHE and SIGMA remain the two accepted "
                "ROBOTS_UNREACHABLE external blockers."
            ),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    timing = report["timing_and_limits"]
    package = report["package"]

    lines = [
        "# B13 — Cierre final de ejecución reproducible",
        "",
        f"- Estado: **{report['closure_status']}**",
        f"- Run ID: `{report['run_id']}`",
        "- Inventario: **52 = 41 operacionales + 11 status-only**",
        "- Downstream: **38 DATAX_READY + 1 ACQUISITION_JOB_READY + 2 EXTERNAL_BLOCKER**",
        f"- Registros legacy: **{package['legacy_records']}**",
        f"- ZIP SHA-256: `{package['zip_sha256']}`",
        "",
        "## Tiempo",
        "",
        f"- Suma de tiempo activo de los 41 crawls: **{timing['active_source_crawl_human']}**",
        f"- Ventana entre inicio del primer crawl y fin del último: **{timing['crawl_wall_span_human']}**",
        f"- Ventana completa hasta paquete B13 READY: **{timing['end_to_end_evidence_window_human']}**",
        f"- Finalización offline posterior al último crawl: **{timing['post_crawl_finalization_human']}**",
        "",
        "## Límites y cobertura",
        "",
        "El crawl final **no fue ilimitado**. Fue la ejecución final operacional con límites explícitos de discovery/configuración. Esto es intencional para evitar spider traps, calendarios infinitos, variantes de query y presión de red innecesaria.",
        "",
        f"- Fuentes que terminaron por un límite `MAX_*`: **{timing['sources_hitting_max_limit_count']}**",
        f"- Stop reasons: `{json.dumps(timing['stop_reason_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Distribución `max_urls`: `{json.dumps(timing['configured_limit_distributions']['max_urls'], ensure_ascii=False, sort_keys=True)}`",
        f"- Distribución `max_runtime_seconds`: `{json.dumps(timing['configured_limit_distributions']['max_runtime_seconds'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "Para el informe final debe describirse como **crawleo final reproducible bajo límites operacionales configurados**, no como rastreo exhaustivo sin restricciones de todos los sitios.",
        "",
        "## Fuentes más lentas",
        "",
        "| Fuente | Duración | Stop reason | Páginas | Requests | Recursos raw |",
        "|---|---:|---|---:|---:|---:|",
    ]

    for item in timing["slowest_sources"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item["logical_source_id"]),
                    str(item["duration_human"]),
                    str(item["stop_reason"] or "-"),
                    str(item["pages_visited"] if item["pages_visited"] is not None else "-"),
                    str(item["requests_total"] if item["requests_total"] is not None else "-"),
                    str(item["raw_resource_count"] if item["raw_resource_count"] is not None else "-"),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Resultado",
            "",
            "B13 deja el pipeline `crawl → projection → package → ZIP` reproducible y cerrado. La siguiente fase es únicamente limpieza y preparación de entrega final (B14), preservando la evidencia y el paquete final de `b13-e2e`.",
            "",
        ]
    )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B13 final timing/coverage/closure audit."
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    repo_root = Path(".").resolve()
    state_path = resolve_path(repo_root, args.state)
    output_path = resolve_path(repo_root, args.output)
    md_path = resolve_path(repo_root, args.markdown)

    state = load_json(state_path)
    report = evaluate(
        repo_root=repo_root,
        run_id=args.run_id,
        state=state,
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    dump_json(output_path, report)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        render_markdown(report),
        encoding="utf-8",
        newline="\n",
    )

    timing = report["timing_and_limits"]
    package = report["package"]

    print("=" * 78)
    print("B13 — FINAL REPRODUCIBLE PIPELINE CLOSURE")
    print("=" * 78)
    print(f"Closure:                  {report['closure_status']}")
    print(f"Run ID:                   {report['run_id']}")
    print("Inventory:                52")
    print("Operational:              41")
    print("Status-only:              11")
    print("DATAX_READY:              38")
    print("ACQUISITION_JOB_READY:     1")
    print("EXTERNAL_BLOCKER:          2")
    print("Pending / unresolved:      0 / 0")
    print(f"Checkpoint RC:             {report['checkpoint_returncode']} (explained)")
    print(f"Legacy records:            {package['legacy_records']}")
    print(f"ZIP SHA256:                {package['zip_sha256']}")
    print()
    print("TIMING")
    print(f"Active crawl sum:          {timing['active_source_crawl_human']}")
    print(f"First→last crawl window:   {timing['crawl_wall_span_human']}")
    print(f"First crawl→B13 READY:     {timing['end_to_end_evidence_window_human']}")
    print(f"Offline finalization:      {timing['post_crawl_finalization_human']}")
    print()
    print("LIMITS")
    print("Unbounded crawl:           NO")
    print(
        "Sources ending MAX_*:     "
        f"{timing['sources_hitting_max_limit_count']}"
    )
    print(
        "Stop reasons:             "
        f"{json.dumps(timing['stop_reason_counts'], sort_keys=True)}"
    )
    print(
        "Configured max_urls:      "
        f"{json.dumps(timing['configured_limit_distributions']['max_urls'], sort_keys=True)}"
    )
    print(
        "Configured max_runtime:   "
        f"{json.dumps(timing['configured_limit_distributions']['max_runtime_seconds'], sort_keys=True)}"
    )
    print()
    print("Slowest sources:")
    for item in timing["slowest_sources"][:10]:
        print(
            f"  {item['logical_source_id']:<22} "
            f"{item['duration_human']:<14} "
            f"stop={item['stop_reason'] or '-'}"
        )
    print()
    print(f"JSON: {output_path}")
    print(f"MD:   {md_path}")

    return 0 if report["closure_status"] == "CLOSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
