from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from apps.source_mapping.main import (
    canonical_host,
    inspect_probe_output,
    parse_checkpoint_resources,
    parse_request_trace,
    split_http_codes,
)


RESOURCE_URL_KEYS = ("normalized_url", "raw_url", "url", "url_descarga", "download_url")


def normalized_entrypoint(url: str) -> str:
    return url.rstrip("/")


def allowed_hosts_for(url: str) -> list[str]:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return []
    base = host[4:] if host.startswith("www.") else host
    return list(dict.fromkeys([host, base, f"www.{base}"]))


def load_inventory(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"Inventario inválido o vacío: {path}")
    return sources


def load_mapping_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("logical_sources") != 52:
        raise ValueError(
            f"Se esperaba mapping de 52 fuentes; recibido: {data.get('logical_sources')}"
        )
    results = data.get("results")
    if not isinstance(results, list) or len(results) != 52:
        raise ValueError("Mapping report no contiene 52 resultados")
    return data


def group_inventory_by_entrypoint(
    inventory: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in inventory:
        grouped[normalized_entrypoint(source["entrypoint"])].append(source)
    return dict(grouped)


def mapping_by_source_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["source_id"]: row for row in report["results"]}


def baseline_for_group(
    group: list[dict[str, Any]],
    mapping_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = [mapping_index[s["source_id"]] for s in group if s["source_id"] in mapping_index]
    if not rows:
        raise ValueError(f"Sin mapping previo para entrypoint {group[0]['entrypoint']}")

    rank = {
        "GREEN_RESOURCES": 3,
        "REACHABLE_NO_RESOURCES": 2,
        "EXECUTION_ERROR": 1,
    }
    return max(rows, key=lambda r: rank.get(r.get("status", ""), 0))


def first_stage_for_baseline(status: str) -> str:
    if status == "EXECUTION_ERROR":
        return "recovery_http"
    return "expanded_html"


def build_stage_config(
    source: dict[str, Any],
    *,
    stage: str,
    max_requests: int,
    max_runtime_seconds: int,
    rate_limit_seconds: float,
    max_depth: int,
    max_urls: int,
) -> dict[str, Any]:
    if stage not in {"recovery_http", "expanded_html", "api_enriched"}:
        raise ValueError(f"Stage desconocido: {stage}")

    entrypoint = source["entrypoint"]
    enable_sitemap = stage == "expanded_html"
    enable_api = stage == "api_enriched"

    cfg = {
        "source_id": source["source_id"],
        "name": source["name"],
        "entrypoint": entrypoint,
        "workflow": "html",
        "seeds": [entrypoint],
        "update_category": "MONTHLY",
        "allowed_extensions": [
            ".pdf", ".xlsx", ".xls", ".ods", ".csv",
            ".json", ".xml", ".zip",
        ],
        "excluded_path_keywords": [],
        "ignore_robots_txt": False,
        "robots_override_reason": None,
        "rate_limit_seconds": rate_limit_seconds,
        "allowed_hosts": allowed_hosts_for(entrypoint),
        "max_depth": max_depth,
        "max_urls": max_urls,
        "max_runtime_seconds": max_runtime_seconds,
        "max_requests": max_requests,
        "max_query_variants": 6,
        "max_consecutive_errors": 3,
        "max_redirects": 6 if stage == "recovery_http" else 4,
        "max_calendar_variants": 6,
        "max_url_length": 2048,
        "max_query_keys": 10,
        "pagination_min_pages": 2,
        "pagination_empty_streak": 2,
        "pagination_window": 4,
        "discover_sitemaps": enable_sitemap,
        "max_sitemap_documents": 4 if enable_sitemap else 0,
        "max_sitemap_urls": 250 if enable_sitemap else 0,
        "max_sitemap_bytes": 750000,
        "discover_apis": enable_api,
        "max_api_endpoints": 12 if enable_api else 0,
        "max_api_response_bytes": 750000,
        "follow_api_pagination": enable_api,
        "max_api_pages": 2 if enable_api else 1,
        "max_api_records_sampled": 50 if enable_api else 10,
        "max_api_stagnant_pages": 1,
        "probe_api_documentation": enable_api,
        "max_api_documents": 4 if enable_api else 0,
        "max_api_document_depth": 1 if enable_api else 0,
        "max_api_document_bytes": 750000,
    }

    return {"sources": [cfg]}


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def inspect_resource_evidence(output_root: Path) -> dict[str, Any]:
    urls: set[str] = set()
    methods: Counter[str] = Counter()
    types: Counter[str] = Counter()
    api_resources = 0

    if not output_root.exists():
        return {
            "resource_urls": [],
            "resource_methods": {},
            "resource_types": {},
            "api_resources": 0,
        }

    for path in output_root.rglob("*.json"):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        for node in _walk(obj):
            if not isinstance(node, dict):
                continue
            resources = node.get("resources")
            if not isinstance(resources, list):
                continue

            for item in resources:
                if not isinstance(item, dict):
                    continue
                url = None
                for key in RESOURCE_URL_KEYS:
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        url = value.strip()
                        break
                if url:
                    urls.add(url)

                method = item.get("discovery_method")
                if isinstance(method, str) and method:
                    methods[method] += 1

                rtype = item.get("resource_type")
                if isinstance(rtype, str) and rtype:
                    types[rtype] += 1

                api_value = item.get("api")
                if (
                    api_value not in (None, False, "", [], {})
                    or (isinstance(method, str) and "api" in method.lower())
                    or (isinstance(rtype, str) and "api" in rtype.lower())
                ):
                    api_resources += 1

    return {
        "resource_urls": sorted(urls),
        "resource_methods": dict(sorted(methods.items())),
        "resource_types": dict(sorted(types.items())),
        "api_resources": api_resources,
    }


def diagnostic_reason(log_text: str, trace: list[dict[str, Any]]) -> str | None:
    lower = log_text.lower()
    _, site_codes = split_http_codes(trace)

    if 403 in site_codes:
        return "SITE_HTTP_403"
    if 401 in site_codes:
        return "SITE_HTTP_401"
    if any(code >= 500 for code in site_codes):
        return "SITE_HTTP_5XX"

    patterns = [
        ("DNS_OR_NAME_RESOLUTION", (
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo failed",
            "name resolution",
        )),
        ("CONNECTION_REFUSED", ("connection refused",)),
        ("CONNECT_TIMEOUT", ("connecttimeout", "connect timeout")),
        ("READ_TIMEOUT", ("readtimeout", "read timeout")),
        ("SSL_ERROR", ("ssl", "certificate verify failed", "tls")),
        ("REDIRECT_PROBLEM", ("too many redirects", "redirect loop")),
    ]
    for reason, tokens in patterns:
        if any(token in lower for token in tokens):
            return reason
    return None


def classify_stage(
    *,
    returncode: int,
    execution_status: str | None,
    resources_found: int,
    trace: list[dict[str, Any]],
    log_text: str,
    timed_out: bool,
) -> tuple[str, str | None]:
    robots_codes, site_codes = split_http_codes(trace)
    reason = diagnostic_reason(log_text, trace)

    if resources_found > 0:
        return "SUCCESS_RESOURCES", reason
    if timed_out:
        return "TIMEOUT", "SUBPROCESS_TIMEOUT"
    if site_codes and any(200 <= code < 400 for code in site_codes):
        if returncode == 0 and (execution_status or "").upper() not in {"FAILED", "ERROR"}:
            return "REACHABLE_NO_RESOURCES", reason
        return "EXECUTION_ERROR", reason
    if site_codes and 403 in site_codes:
        return "ACCESS_RESTRICTED", "SITE_HTTP_403"
    if not site_codes and any(code in {401, 403} for code in robots_codes):
        return "ROBOTS_REVIEW", "ROBOTS_ACCESS_RESTRICTED"
    if reason in {"DNS_OR_NAME_RESOLUTION", "CONNECTION_REFUSED", "CONNECT_TIMEOUT", "SSL_ERROR"}:
        return "UNAVAILABLE", reason
    if returncode != 0 or (execution_status or "").upper() in {"FAILED", "ERROR"}:
        return "EXECUTION_ERROR", reason
    return "NO_EVIDENCE", reason


def stage_result_path(stage_dir: Path) -> Path:
    return stage_dir / "stage_result.json"


def run_stage(
    source: dict[str, Any],
    *,
    stage: str,
    report_dir: Path,
    python_executable: str,
    max_requests: int,
    max_runtime_seconds: int,
    rate_limit_seconds: float,
    max_depth: int,
    max_urls: int,
    subprocess_timeout: int,
    force: bool,
) -> dict[str, Any]:
    source_root = report_dir / "physical_sources" / source["source_id"]
    stage_dir = source_root / stage
    result_file = stage_result_path(stage_dir)

    if result_file.exists() and not force:
        result = json.loads(result_file.read_text(encoding="utf-8"))
        result["resumed"] = True
        return result

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    config_path = stage_dir / "probe.yaml"
    crawl_dir = stage_dir / "crawl"
    log_path = stage_dir / "crawl.log"

    payload = build_stage_config(
        source,
        stage=stage,
        max_requests=max_requests,
        max_runtime_seconds=max_runtime_seconds,
        rate_limit_seconds=rate_limit_seconds,
        max_depth=max_depth,
        max_urls=max_urls,
    )
    config_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    cmd = [
        python_executable,
        "-m",
        "apps.crawler_batch.main",
        "--config",
        str(config_path),
        "--source",
        source["source_id"],
        "--output-dir",
        str(crawl_dir),
        "--force",
    ]

    started = time.monotonic()
    timed_out = False

    try:
        process = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=subprocess_timeout,
        )
        returncode = process.returncode
        stdout = process.stdout or ""
        stderr = process.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = (
            exc.stdout.decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode("utf-8", "replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )

    log_text = stdout + "\n" + stderr
    log_path.write_text(log_text, encoding="utf-8")

    inspected = inspect_probe_output(crawl_dir)
    checkpoint_count = parse_checkpoint_resources(log_text) or 0
    evidence = inspect_resource_evidence(crawl_dir)
    resources_found = max(
        inspected.get("resources_found") or 0,
        checkpoint_count,
        len(evidence["resource_urls"]),
    )

    trace = parse_request_trace(log_text)
    robots_codes, site_codes = split_http_codes(trace)
    stage_status, reason = classify_stage(
        returncode=returncode,
        execution_status=inspected.get("execution_status"),
        resources_found=resources_found,
        trace=trace,
        log_text=log_text,
        timed_out=timed_out,
    )

    result = {
        "stage": stage,
        "status": stage_status,
        "diagnostic_reason": reason,
        "resources_found": resources_found,
        "api_resources": evidence["api_resources"],
        "resource_methods": evidence["resource_methods"],
        "resource_types": evidence["resource_types"],
        "robots_http_codes": robots_codes,
        "site_http_codes": site_codes,
        "requests_reported": inspected.get("requests_reported"),
        "execution_status": inspected.get("execution_status"),
        "stop_reason": inspected.get("stop_reason"),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "returncode": returncode,
        "timed_out": timed_out,
        "config_file": str(config_path),
        "crawl_output_dir": str(crawl_dir),
        "log_file": str(log_path),
        "resumed": False,
    }
    result_file.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return result


def should_continue_after(stage_result: dict[str, Any]) -> bool:
    return stage_result["status"] == "REACHABLE_NO_RESOURCES"


def final_characterization(stages: list[dict[str, Any]]) -> dict[str, Any]:
    if not stages:
        return {
            "status": "NO_EVIDENCE",
            "suggested_workflow": None,
            "recommended_next_phase": "MANUAL_REVIEW",
            "winning_stage": None,
        }

    resource_stage = next(
        (stage for stage in stages if stage["resources_found"] > 0),
        None,
    )
    if resource_stage is not None:
        methods = {
            method.lower()
            for method in resource_stage.get("resource_methods", {}).keys()
        }
        if (
            resource_stage.get("api_resources", 0) > 0
            or any("api" in method for method in methods)
        ):
            status = "API_CANDIDATE"
            workflow = "api"
        elif any("sitemap" in method for method in methods):
            status = "HTML_SITEMAP_CANDIDATE"
            workflow = "html"
        else:
            status = "HTTP_HTML_CANDIDATE"
            workflow = "html"

        return {
            "status": status,
            "suggested_workflow": workflow,
            "recommended_next_phase": "B6_OPERATIONAL_CONFIG",
            "winning_stage": resource_stage["stage"],
        }

    last = stages[-1]
    all_statuses = [stage["status"] for stage in stages]

    if "ACCESS_RESTRICTED" in all_statuses:
        return {
            "status": "ACCESS_RESTRICTED",
            "suggested_workflow": None,
            "recommended_next_phase": "ACCESS_REVIEW",
            "winning_stage": None,
        }
    if "ROBOTS_REVIEW" in all_statuses:
        return {
            "status": "ROBOTS_REVIEW",
            "suggested_workflow": None,
            "recommended_next_phase": "ROBOTS_POLICY_REVIEW",
            "winning_stage": None,
        }
    if "UNAVAILABLE" in all_statuses:
        return {
            "status": "UNAVAILABLE",
            "suggested_workflow": None,
            "recommended_next_phase": "SOURCE_AVAILABILITY_REVIEW",
            "winning_stage": None,
        }
    if last["status"] == "REACHABLE_NO_RESOURCES":
        # Deliberadamente NO asignar javascript automáticamente.
        return {
            "status": "REACHABLE_NEEDS_DEEPER_REVIEW",
            "suggested_workflow": None,
            "recommended_next_phase": "B7_OR_CUSTOM_REVIEW",
            "winning_stage": None,
        }

    return {
        "status": "EXECUTION_ERROR",
        "suggested_workflow": None,
        "recommended_next_phase": "DIAGNOSTIC_REVIEW",
        "winning_stage": None,
    }


def stages_for_source(
    baseline_status: str,
    *,
    source: dict[str, Any],
    report_dir: Path,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []

    first = first_stage_for_baseline(baseline_status)
    first_result = run_stage(
        source,
        stage=first,
        report_dir=report_dir,
        python_executable=sys.executable,
        max_requests=args.recovery_requests if first == "recovery_http" else args.html_requests,
        max_runtime_seconds=args.max_runtime,
        rate_limit_seconds=args.rate_limit,
        max_depth=1 if first == "recovery_http" else args.html_depth,
        max_urls=args.max_urls,
        subprocess_timeout=args.subprocess_timeout,
        force=args.force,
    )
    stages.append(first_result)

    if first_result["resources_found"] > 0:
        return stages

    if first == "recovery_http" and first_result["status"] == "REACHABLE_NO_RESOURCES":
        html_result = run_stage(
            source,
            stage="expanded_html",
            report_dir=report_dir,
            python_executable=sys.executable,
            max_requests=args.html_requests,
            max_runtime_seconds=args.max_runtime,
            rate_limit_seconds=args.rate_limit,
            max_depth=args.html_depth,
            max_urls=args.max_urls,
            subprocess_timeout=args.subprocess_timeout,
            force=args.force,
        )
        stages.append(html_result)
        if html_result["resources_found"] > 0:
            return stages
        if not should_continue_after(html_result):
            return stages

    elif first != "recovery_http":
        if not should_continue_after(first_result):
            return stages

    api_result = run_stage(
        source,
        stage="api_enriched",
        report_dir=report_dir,
        python_executable=sys.executable,
        max_requests=args.api_requests,
        max_runtime_seconds=args.max_runtime,
        rate_limit_seconds=args.rate_limit,
        max_depth=1,
        max_urls=args.max_urls,
        subprocess_timeout=args.subprocess_timeout,
        force=args.force,
    )
    stages.append(api_result)
    return stages


def build_logical_rows(
    inventory: list[dict[str, Any]],
    physical_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in inventory:
        key = normalized_entrypoint(source["entrypoint"])
        physical = physical_results[key]
        rows.append(
            {
                "source_id": source["source_id"],
                "logical_code": source["logical_code"],
                "name": source["name"],
                "entrypoint": source["entrypoint"],
                "host_key": source["host_key"],
                "physical_probe_source_id": physical["physical_probe_source_id"],
                "baseline_status": physical["baseline_status"],
                "characterization_status": physical["characterization"]["status"],
                "suggested_workflow": physical["characterization"]["suggested_workflow"],
                "recommended_next_phase": physical["characterization"]["recommended_next_phase"],
                "winning_stage": physical["characterization"]["winning_stage"],
                "stages": physical["stages"],
            }
        )
    return rows


def write_reports(
    *,
    report_dir: Path,
    physical_results: dict[str, dict[str, Any]],
    logical_rows: list[dict[str, Any]],
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    status_counts = Counter(row["characterization_status"] for row in logical_rows)
    workflow_counts = Counter(
        row["suggested_workflow"] or "UNRESOLVED"
        for row in logical_rows
    )
    next_phase_counts = Counter(row["recommended_next_phase"] for row in logical_rows)

    payload = {
        "schema_version": "source-characterization-report-1.0",
        "generated_at_utc": generated_at,
        "logical_sources": len(logical_rows),
        "physical_entrypoints": len(physical_results),
        "summary_by_status": dict(sorted(status_counts.items())),
        "summary_by_workflow_hint": dict(sorted(workflow_counts.items())),
        "summary_by_next_phase": dict(sorted(next_phase_counts.items())),
        "physical_results": list(physical_results.values()),
        "logical_results": logical_rows,
    }
    (report_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (report_dir / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fieldnames = [
            "logical_code", "source_id", "entrypoint", "baseline_status",
            "characterization_status", "suggested_workflow",
            "recommended_next_phase", "winning_stage",
            "physical_probe_source_id",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in logical_rows:
            writer.writerow({key: row.get(key) for key in fieldnames})

    candidate_yaml = {
        "schema_version": "source-characterization-candidates-1.0",
        "warning": (
            "Hints generados por evidencia. No sustituye config/sources.yaml. "
            "No asigna JavaScript automáticamente."
        ),
        "sources": [
            {
                "source_id": row["source_id"],
                "logical_code": row["logical_code"],
                "entrypoint": row["entrypoint"],
                "baseline_status": row["baseline_status"],
                "characterization_status": row["characterization_status"],
                "suggested_workflow": row["suggested_workflow"],
                "recommended_next_phase": row["recommended_next_phase"],
                "winning_stage": row["winning_stage"],
            }
            for row in logical_rows
        ],
    }
    (report_dir / "candidate_workflows.yaml").write_text(
        yaml.safe_dump(candidate_yaml, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )

    lines = [
        "# B6B — Caracterización masiva HTTP/API",
        "",
        f"- Generado UTC: `{generated_at}`",
        f"- Fuentes lógicas: **{len(logical_rows)}**",
        f"- Entrypoints físicos: **{len(physical_results)}**",
        "",
        "## Estados",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")

    lines.extend([
        "",
        "## Workflow hints",
        "",
        "| Hint | Cantidad |",
        "|---|---:|",
    ])
    for workflow, count in sorted(workflow_counts.items()):
        lines.append(f"| {workflow} | {count} |")

    lines.extend([
        "",
        "## Fuentes",
        "",
        "| Código | Baseline | Caracterización | Workflow | Siguiente |",
        "|---|---|---|---|---|",
    ])
    for row in logical_rows:
        lines.append(
            f"| {row['logical_code']} | {row['baseline_status']} | "
            f"{row['characterization_status']} | "
            f"{row['suggested_workflow'] or '-'} | "
            f"{row['recommended_next_phase']} |"
        )

    (report_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "B6B: caracterización HTTP/API por capas, reutilizando el mapeo 52/52."
        )
    )
    parser.add_argument("--inventory", default="config/source_inventory.yaml")
    parser.add_argument(
        "--mapping-report",
        default=".runtime/source_mapping/latest.json",
    )
    parser.add_argument(
        "--report-dir",
        default=".runtime/source_characterization",
    )
    parser.add_argument("--html-requests", type=int, default=12)
    parser.add_argument("--api-requests", type=int, default=15)
    parser.add_argument("--recovery-requests", type=int, default=8)
    parser.add_argument("--max-runtime", type=int, default=45)
    parser.add_argument("--subprocess-timeout", type=int, default=60)
    parser.add_argument("--rate-limit", type=float, default=1.0)
    parser.add_argument("--html-depth", type=int, default=2)
    parser.add_argument("--max-urls", type=int, default=150)
    parser.add_argument("--inter-source-delay", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reejecuta stages aunque exista stage_result.json.",
    )
    args = parser.parse_args()

    inventory = load_inventory(Path(args.inventory))
    mapping_report = load_mapping_report(Path(args.mapping_report))
    mapping_index = mapping_by_source_id(mapping_report)
    grouped = group_inventory_by_entrypoint(inventory)

    physical_items = list(grouped.items())
    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit debe ser > 0")
        physical_items = physical_items[: args.limit]

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("B6B — CARACTERIZACIÓN MASIVA HTTP/API POR CAPAS")
    print("=" * 78)
    print(f"Fuentes lógicas inventario: {len(inventory)}")
    print(f"Entrypoints físicos totales: {len(grouped)}")
    print(f"Entrypoints a procesar: {len(physical_items)}")
    print("Stages posibles: recovery_http → expanded_html → api_enriched")
    print("Sitemaps: sí en expanded_html")
    print("API discovery: sí en api_enriched")
    print("Browser/JavaScript: NO")
    print("Binarios: NO")
    print("Resume por defecto: SÍ")
    print()

    physical_results: dict[str, dict[str, Any]] = {}

    for idx, (entrypoint, group) in enumerate(physical_items, 1):
        baseline = baseline_for_group(group, mapping_index)
        probe_source = group[0]

        print(
            f"[{idx:02d}/{len(physical_items):02d}] "
            f"{probe_source['logical_code']:<20} "
            f"baseline={baseline['status']:<24} "
            f"host={probe_source['host_key']}"
        )

        stages = stages_for_source(
            baseline["status"],
            source=probe_source,
            report_dir=report_dir,
            args=args,
        )
        characterization = final_characterization(stages)

        for stage in stages:
            print(
                f"    {stage['stage']:<16} "
                f"{stage['status']:<24} "
                f"resources={stage['resources_found']:<4} "
                f"api={stage['api_resources']:<3} "
                f"site={stage['site_http_codes']} "
                f"{'(resume)' if stage.get('resumed') else ''}"
            )

        print(
            f"    => {characterization['status']} "
            f"workflow={characterization['suggested_workflow'] or '-'} "
            f"next={characterization['recommended_next_phase']}"
        )

        physical_results[entrypoint] = {
            "entrypoint": entrypoint,
            "host_key": probe_source["host_key"],
            "physical_probe_source_id": probe_source["source_id"],
            "logical_source_ids": [s["source_id"] for s in group],
            "logical_codes": [s["logical_code"] for s in group],
            "baseline_status": baseline["status"],
            "stages": stages,
            "characterization": characterization,
        }

        if args.inter_source_delay > 0 and idx < len(physical_items):
            time.sleep(args.inter_source_delay)

    if len(physical_items) == len(grouped):
        logical_rows = build_logical_rows(inventory, physical_results)
    else:
        included_entrypoints = set(physical_results)
        limited_inventory = [
            source
            for source in inventory
            if normalized_entrypoint(source["entrypoint"]) in included_entrypoints
        ]
        logical_rows = build_logical_rows(limited_inventory, physical_results)

    write_reports(
        report_dir=report_dir,
        physical_results=physical_results,
        logical_rows=logical_rows,
    )

    status_counts = Counter(row["characterization_status"] for row in logical_rows)
    workflow_counts = Counter(
        row["suggested_workflow"] or "UNRESOLVED"
        for row in logical_rows
    )

    print()
    print("=" * 78)
    print("B6B COMPLETADO")
    print("=" * 78)
    print(f"Fuentes lógicas caracterizadas: {len(logical_rows)}")
    print(f"Entrypoints físicos:             {len(physical_results)}")
    print()
    print("Estados:")
    for key, count in sorted(status_counts.items()):
        print(f"  {key:<34} {count}")
    print()
    print("Workflow hints:")
    for key, count in sorted(workflow_counts.items()):
        print(f"  {key:<34} {count}")
    print()
    print(f"JSON:       {report_dir / 'latest.json'}")
    print(f"CSV:        {report_dir / 'latest.csv'}")
    print(f"MD:         {report_dir / 'latest.md'}")
    print(f"Candidates: {report_dir / 'candidate_workflows.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
