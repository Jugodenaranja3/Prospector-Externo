from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import yaml


URL_KEYS = ("normalized_url", "raw_url", "url", "url_descarga", "download_url")


def canonical_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


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


def build_probe_config(
    source: dict[str, Any],
    *,
    max_requests: int,
    max_runtime_seconds: int,
    rate_limit_seconds: float,
    max_depth: int,
    max_urls: int,
) -> dict[str, Any]:
    entrypoint = source["entrypoint"]
    return {
        "sources": [
            {
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
                "max_query_variants": 4,
                "max_consecutive_errors": 2,
                "max_redirects": 3,
                "max_calendar_variants": 4,
                "max_url_length": 2048,
                "max_query_keys": 8,
                "pagination_min_pages": 2,
                "pagination_empty_streak": 2,
                "pagination_window": 3,
                # Baseline barato: mide capacidad HTTP/HTML actual.
                "discover_sitemaps": False,
                "max_sitemap_documents": 0,
                "max_sitemap_urls": 0,
                "max_sitemap_bytes": 250000,
                "discover_apis": False,
                "max_api_endpoints": 0,
                "max_api_response_bytes": 300000,
                "follow_api_pagination": False,
                "max_api_pages": 1,
                "max_api_records_sampled": 10,
                "max_api_stagnant_pages": 1,
                "probe_api_documentation": False,
                "max_api_documents": 0,
                "max_api_document_depth": 0,
                "max_api_document_bytes": 300000,
            }
        ]
    }


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def inspect_probe_output(output_root: Path) -> dict[str, Any]:
    urls: set[str] = set()
    execution_statuses: list[str] = []
    stop_reasons: list[str] = []
    request_counts: list[int] = []

    if not output_root.exists():
        return {
            "resources_found": 0,
            "execution_status": None,
            "stop_reason": None,
            "requests_reported": None,
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
            if isinstance(resources, list):
                for item in resources:
                    if not isinstance(item, dict):
                        continue
                    for key in URL_KEYS:
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            urls.add(value.strip())
                            break

            status = node.get("execution_status")
            if isinstance(status, str):
                execution_statuses.append(status)

            stop = node.get("stop_reason")
            if isinstance(stop, str):
                stop_reasons.append(stop)

            for key in ("requests_total", "request_count", "requests"):
                count = node.get(key)
                if isinstance(count, int) and not isinstance(count, bool):
                    request_counts.append(count)

    return {
        "resources_found": len(urls),
        "execution_status": execution_statuses[-1] if execution_statuses else None,
        "stop_reason": stop_reasons[-1] if stop_reasons else None,
        "requests_reported": max(request_counts) if request_counts else None,
    }


HTTP_REQUEST_RE = re.compile(
    r'HTTP Request:\s+(?P<method>[A-Z]+)\s+(?P<url>\S+)\s+"HTTP/\d(?:\.\d)?\s+(?P<code>\d{3})',
    flags=re.IGNORECASE,
)


def parse_request_trace(log_text: str) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for match in HTTP_REQUEST_RE.finditer(log_text):
        url = match.group("url")
        parsed = urlparse(url)
        path = (parsed.path or "/").rstrip("/") or "/"
        trace.append(
            {
                "method": match.group("method").upper(),
                "url": url,
                "status_code": int(match.group("code")),
                "is_robots": path.lower().endswith("/robots.txt"),
                "host": canonical_host(url),
            }
        )
    return trace


def parse_checkpoint_resources(log_text: str) -> int | None:
    matches = re.findall(r"\((\d+)\s+recursos\)", log_text, flags=re.IGNORECASE)
    return int(matches[-1]) if matches else None


def split_http_codes(trace: list[dict[str, Any]]) -> tuple[list[int], list[int]]:
    robots = sorted({item["status_code"] for item in trace if item["is_robots"]})
    site = sorted({item["status_code"] for item in trace if not item["is_robots"]})
    return robots, site


def classify_probe(
    *,
    returncode: int,
    log_text: str,
    resources_found: int,
    execution_status: str | None,
    timed_out: bool,
    request_trace: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    trace = request_trace if request_trace is not None else parse_request_trace(log_text)
    robots_codes, site_codes = split_http_codes(trace)
    lower = log_text.lower()

    if timed_out:
        return "TIMEOUT", "review_reachability_or_runtime"

    # Robots se trata aparte; un 403/404 de robots.txt no se confunde
    # con el estado HTTP del entrypoint.
    if not site_codes and any(code in {401, 403} for code in robots_codes):
        return "ROBOTS_BLOCKED", "review_robots_policy"

    if resources_found > 0:
        return "GREEN_RESOURCES", "candidate_for_b6_http_html"

    if execution_status and execution_status.upper() in {"FAILED", "ERROR"}:
        if site_codes and not any(200 <= code < 400 for code in site_codes):
            if any(code == 403 for code in site_codes):
                return "HTTP_403", "review_access_or_waf"
            if any(code == 404 for code in site_codes):
                return "HTTP_404", "review_entrypoint"
        if any(
            token in lower
            for token in (
                "name or service not known",
                "nodename nor servname",
                "getaddrinfo failed",
                "connection refused",
                "connecterror",
                "dns",
                "ssl error",
            )
        ):
            return "UNAVAILABLE", "review_domain_or_network"
        return "EXECUTION_ERROR", "inspect_persisted_output"

    if returncode != 0:
        if site_codes and not any(200 <= code < 400 for code in site_codes):
            if 403 in site_codes:
                return "HTTP_403", "review_access_or_waf"
            if 404 in site_codes:
                return "HTTP_404", "review_entrypoint"
        return "EXECUTION_ERROR", "inspect_persisted_output"

    if any(200 <= code < 400 for code in site_codes):
        return "REACHABLE_NO_RESOURCES", "review_seed_or_workflow"

    if 403 in site_codes:
        return "HTTP_403", "review_access_or_waf"
    if 404 in site_codes:
        return "HTTP_404", "review_entrypoint"

    if not site_codes and any(code in {404, 405} for code in robots_codes):
        return "REACHABLE_NO_RESOURCES", "review_seed_or_workflow"

    return "EXECUTION_ERROR", "inspect_persisted_output"


def error_excerpt(text: str, max_chars: int = 800) -> str | None:
    interesting: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(
            token in low
            for token in (
                "critical", "error", "exception", "forbidden", "timeout",
                "bloque", "blocked", "disallow", " 404 ", " 403 ", " 500 ",
            )
        ):
            interesting.append(line.strip())
    if not interesting:
        return None
    joined = " | ".join(interesting[-6:])
    return joined[-max_chars:]


def safe_remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def probe_source(
    source: dict[str, Any],
    *,
    python_executable: str,
    report_dir: Path,
    max_requests: int,
    max_runtime_seconds: int,
    rate_limit_seconds: float,
    max_depth: int,
    max_urls: int,
    subprocess_timeout: int,
) -> dict[str, Any]:
    started = time.monotonic()
    timed_out = False

    crawl_root = report_dir / "crawls" / source["source_id"]
    log_path = report_dir / "logs" / f"{source['source_id']}.log"
    config_path = report_dir / "probe_configs" / f"{source['source_id']}.yaml"

    crawl_root.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Nunca mezclar evidencia de una corrida previa.
    safe_remove_tree(crawl_root)
    crawl_root.mkdir(parents=True, exist_ok=True)

    payload = build_probe_config(
        source,
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
        str(crawl_root),
        "--force",
    ]

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
        log_text = (process.stdout or "") + "\n" + (process.stderr or "")
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

    log_path.write_text(log_text, encoding="utf-8", errors="replace")

    inspected = inspect_probe_output(crawl_root)
    checkpoint_count = parse_checkpoint_resources(log_text)
    resources_found = max(inspected["resources_found"], checkpoint_count or 0)

    trace = parse_request_trace(log_text)
    robots_codes, site_codes = split_http_codes(trace)

    status, next_action = classify_probe(
        returncode=returncode,
        log_text=log_text,
        resources_found=resources_found,
        execution_status=inspected["execution_status"],
        timed_out=timed_out,
        request_trace=trace,
    )

    return {
        "source_id": source["source_id"],
        "logical_code": source["logical_code"],
        "name": source["name"],
        "entrypoint": source["entrypoint"],
        "host_key": source.get("host_key") or canonical_host(source["entrypoint"]),
        "status": status,
        "coverage_level": (
            "L1_RESOURCE_DISCOVERY"
            if status == "GREEN_RESOURCES"
            else "L0_REACHABLE"
            if status == "REACHABLE_NO_RESOURCES"
            else "L_NEGATIVE"
        ),
        "probe_profile": "HTTP_HTML_BASELINE",
        "resources_found": resources_found,
        "robots_http_codes": robots_codes,
        "site_http_codes": site_codes,
        "request_trace": trace,
        "requests_reported": inspected["requests_reported"],
        "execution_status": inspected["execution_status"],
        "stop_reason": inspected["stop_reason"],
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "next_action": next_action,
        "error_excerpt": error_excerpt(log_text),
        "reused_probe_from": None,
        "crawl_output_dir": str(crawl_root),
        "log_file": str(log_path),
        "probe_config_file": str(config_path),
    }


def write_logical_manifest(row: dict[str, Any], report_dir: Path) -> None:
    logical_dir = report_dir / "logical_sources"
    logical_dir.mkdir(parents=True, exist_ok=True)
    path = logical_dir / f"{row['source_id']}.json"
    path.write_text(
        json.dumps(row, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_reports(
    results: list[dict[str, Any]],
    report_dir: Path,
    *,
    physical_probes: int,
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = Counter(r["status"] for r in results)
    coverage = Counter(r["coverage_level"] for r in results)

    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": "source-mapping-report-1.1",
        "generated_at_utc": generated_at,
        "probe_profile": "HTTP_HTML_BASELINE",
        "warning": (
            "Mapeo conservador de capacidad actual. "
            "Los outputs reales de cada probe físico quedan preservados en crawls/."
        ),
        "logical_sources": len(results),
        "physical_unique_entrypoint_probes": physical_probes,
        "summary_by_status": dict(sorted(summary.items())),
        "summary_by_coverage_level": dict(sorted(coverage.items())),
        "results": results,
    }

    (report_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fieldnames = [
        "logical_code", "source_id", "name", "entrypoint", "host_key",
        "status", "coverage_level", "resources_found",
        "robots_http_codes", "site_http_codes",
        "requests_reported", "execution_status", "stop_reason",
        "elapsed_seconds", "next_action", "reused_probe_from",
        "crawl_output_dir", "log_file", "probe_config_file", "error_excerpt",
    ]
    with (report_dir / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            item = dict(row)
            item["robots_http_codes"] = ",".join(
                map(str, row.get("robots_http_codes") or [])
            )
            item["site_http_codes"] = ",".join(
                map(str, row.get("site_http_codes") or [])
            )
            writer.writerow({key: item.get(key) for key in fieldnames})

    lines = [
        "# Prospector Externo — mapeo masivo de fuentes",
        "",
        f"- Generado UTC: `{generated_at}`",
        f"- Fuentes lógicas: **{len(results)}**",
        f"- Probes físicos de entrypoints únicos: **{physical_probes}**",
        "- Perfil: **HTTP_HTML_BASELINE**",
        "- Evidencia física: **preservada en `crawls/`**",
        "",
        "> Este reporte es una línea base, no cobertura definitiva.",
        "",
        "## Resumen",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for key, count in sorted(summary.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Fuentes",
            "",
            "| Código | Estado | Recursos | Robots HTTP | Sitio HTTP | Output |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for row in results:
        robots = ",".join(map(str, row.get("robots_http_codes") or [])) or "-"
        site = ",".join(map(str, row.get("site_http_codes") or [])) or "-"
        output = row.get("crawl_output_dir") or "-"
        if row.get("reused_probe_from"):
            output = f"{output} (reusa {row['reused_probe_from']})"
        lines.append(
            f"| {row['logical_code']} | {row['status']} | "
            f"{row['resources_found']} | {robots} | {site} | `{output}` |"
        )

    (report_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    for row in results:
        write_logical_manifest(row, report_dir)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Mapeo masivo conservador de fuentes con evidencia real persistida "
            "fuera de Git."
        )
    )
    parser.add_argument(
        "--inventory",
        default="config/source_inventory.yaml",
    )
    parser.add_argument(
        "--report-dir",
        default=".runtime/source_mapping",
    )
    parser.add_argument("--max-requests", type=int, default=5)
    parser.add_argument("--max-runtime", type=int, default=20)
    parser.add_argument("--subprocess-timeout", type=int, default=35)
    parser.add_argument("--rate-limit", type=float, default=1.0)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-urls", type=int, default=40)
    parser.add_argument("--inter-source-delay", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    sources = load_inventory(Path(args.inventory))
    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit debe ser > 0")
        sources = sources[: args.limit]

    print("=" * 76)
    print("PROSPECTOR EXTERNO — MAPEO 52 FUENTES CON EVIDENCIA PERSISTIDA")
    print("=" * 76)
    print(f"Fuentes lógicas a reportar: {len(sources)}")
    print(
        f"Budget por entrypoint: max_requests={args.max_requests}, "
        f"max_runtime={args.max_runtime}s"
    )
    print("Outputs reales: .runtime/source_mapping/crawls/<source_id>/")
    print("Logs reales:    .runtime/source_mapping/logs/<source_id>.log")
    print("Configs probe:  .runtime/source_mapping/probe_configs/<source_id>.yaml")
    print("Sitemap/API/browser: NO en este baseline")
    print("Binarios: NO se descargan")
    print()

    cache: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    physical_probes = 0

    for idx, source in enumerate(sources, 1):
        key = normalized_entrypoint(source["entrypoint"])

        if key in cache:
            base = dict(cache[key])
            base.update(
                {
                    "source_id": source["source_id"],
                    "logical_code": source["logical_code"],
                    "name": source["name"],
                    "entrypoint": source["entrypoint"],
                    "host_key": source.get("host_key")
                    or canonical_host(source["entrypoint"]),
                    "reused_probe_from": cache[key]["logical_code"],
                }
            )
            row = base
            print(
                f"[{idx:02d}/{len(sources):02d}] "
                f"{source['logical_code']:<20} "
                f"{row['status']:<25} "
                f"reuse={row['reused_probe_from']}"
            )
        else:
            row = probe_source(
                source,
                python_executable=sys.executable,
                report_dir=report_dir,
                max_requests=args.max_requests,
                max_runtime_seconds=args.max_runtime,
                rate_limit_seconds=args.rate_limit,
                max_depth=args.max_depth,
                max_urls=args.max_urls,
                subprocess_timeout=args.subprocess_timeout,
            )
            cache[key] = dict(row)
            physical_probes += 1

            print(
                f"[{idx:02d}/{len(sources):02d}] "
                f"{source['logical_code']:<20} "
                f"{row['status']:<25} "
                f"resources={row['resources_found']:<4} "
                f"robots={row['robots_http_codes']} "
                f"site={row['site_http_codes']} "
                f"t={row['elapsed_seconds']}s"
            )

            if args.inter_source_delay > 0 and idx < len(sources):
                time.sleep(args.inter_source_delay)

        results.append(row)

    write_reports(results, report_dir, physical_probes=physical_probes)

    summary = Counter(r["status"] for r in results)

    print()
    print("=" * 76)
    print("MAPEO COMPLETADO — EVIDENCIA REAL CONSERVADA")
    print("=" * 76)
    print(f"Fuentes lógicas:       {len(results)}")
    print(f"Entrypoints probados:  {physical_probes}")
    for key, count in sorted(summary.items()):
        print(f"{key:<28} {count}")

    print()
    print(f"Resumen JSON: {report_dir / 'latest.json'}")
    print(f"Resumen CSV:  {report_dir / 'latest.csv'}")
    print(f"Resumen MD:   {report_dir / 'latest.md'}")
    print(f"Crawls:       {report_dir / 'crawls'}")
    print(f"Logs:         {report_dir / 'logs'}")
    print(f"Configs:      {report_dir / 'probe_configs'}")
    print(f"Lógicos:      {report_dir / 'logical_sources'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
