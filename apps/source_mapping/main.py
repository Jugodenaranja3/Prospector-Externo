from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    values = [host, base, f"www.{base}"]
    return list(dict.fromkeys(values))


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
                    ".json", ".xml", ".zip"
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


def parse_http_codes(log_text: str) -> list[int]:
    return [int(x) for x in re.findall(r'HTTP/\d(?:\.\d)?\s+(\d{3})', log_text)]


def parse_checkpoint_resources(log_text: str) -> int | None:
    matches = re.findall(r"\((\d+)\s+recursos\)", log_text, flags=re.IGNORECASE)
    return int(matches[-1]) if matches else None


def classify_probe(
    *,
    returncode: int,
    log_text: str,
    resources_found: int,
    execution_status: str | None,
    timed_out: bool,
) -> tuple[str, str]:
    lower = log_text.lower()
    http_codes = parse_http_codes(log_text)

    if timed_out:
        return "TIMEOUT", "review_reachability_or_runtime"

    if (
        ("robots" in lower and ("bloque" in lower or "disallow" in lower or "blocked" in lower))
        and resources_found == 0
    ):
        return "ROBOTS_BLOCKED", "review_robots_policy"

    if 403 in http_codes and resources_found == 0:
        return "HTTP_403", "review_access_or_waf"

    if 404 in http_codes and resources_found == 0:
        return "HTTP_404", "review_entrypoint"

    if any(token in lower for token in (
        "name or service not known",
        "nodename nor servname",
        "getaddrinfo failed",
        "connecterror",
        "connection refused",
        "dns",
        "ssl error",
    )) and resources_found == 0:
        return "UNAVAILABLE", "review_domain_or_network"

    if returncode != 0:
        return "EXECUTION_ERROR", "inspect_error"

    if execution_status and execution_status.upper() in {"FAILED", "ERROR"}:
        return "EXECUTION_ERROR", "inspect_error"

    if resources_found > 0:
        return "GREEN_RESOURCES", "candidate_for_b6_http_html"

    return "REACHABLE_NO_RESOURCES", "review_seed_or_workflow"


def error_excerpt(text: str, max_chars: int = 600) -> str | None:
    interesting = []
    for line in text.splitlines():
        low = line.lower()
        if any(token in low for token in (
            "critical", "error", "exception", "forbidden", "timeout",
            "bloque", "blocked", "disallow", "404", "403"
        )):
            interesting.append(line.strip())
    if not interesting:
        return None
    joined = " | ".join(interesting[-4:])
    return joined[-max_chars:]


def probe_source(
    source: dict[str, Any],
    *,
    python_executable: str,
    max_requests: int,
    max_runtime_seconds: int,
    rate_limit_seconds: float,
    max_depth: int,
    max_urls: int,
    subprocess_timeout: int,
) -> dict[str, Any]:
    started = time.monotonic()
    timed_out = False

    with tempfile.TemporaryDirectory(prefix=f"prospector_map_{source['source_id']}_") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "probe.yaml"
        output_path = tmp_path / "output"

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
            str(output_path),
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
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            log_text = stdout + "\n" + stderr

        inspected = inspect_probe_output(output_path)
        checkpoint_count = parse_checkpoint_resources(log_text)
        resources_found = max(inspected["resources_found"], checkpoint_count or 0)

        status, next_action = classify_probe(
            returncode=returncode,
            log_text=log_text,
            resources_found=resources_found,
            execution_status=inspected["execution_status"],
            timed_out=timed_out,
        )

        http_codes = parse_http_codes(log_text)

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
            "http_codes": sorted(set(http_codes)),
            "requests_reported": inspected["requests_reported"],
            "execution_status": inspected["execution_status"],
            "stop_reason": inspected["stop_reason"],
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "next_action": next_action,
            "error_excerpt": error_excerpt(log_text),
            "reused_probe_from": None,
        }


def write_reports(results: list[dict[str, Any]], report_dir: Path, *, physical_probes: int) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = Counter(r["status"] for r in results)
    coverage = Counter(r["coverage_level"] for r in results)

    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": "source-mapping-report-1.0",
        "generated_at_utc": generated_at,
        "probe_profile": "HTTP_HTML_BASELINE",
        "warning": (
            "Este reporte es un mapeo conservador de capacidad actual, no una validación 100% "
            "ni una asignación definitiva de workflow."
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
        "status", "coverage_level", "resources_found", "http_codes",
        "requests_reported", "execution_status", "stop_reason",
        "elapsed_seconds", "next_action", "reused_probe_from", "error_excerpt",
    ]
    with (report_dir / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            item = dict(row)
            item["http_codes"] = ",".join(map(str, row.get("http_codes") or []))
            writer.writerow({key: item.get(key) for key in fieldnames})

    lines = [
        "# Prospector Externo — mapeo masivo de fuentes",
        "",
        f"- Generado UTC: `{generated_at}`",
        f"- Fuentes lógicas: **{len(results)}**",
        f"- Probes físicos de entrypoints únicos: **{physical_probes}**",
        "- Perfil: **HTTP_HTML_BASELINE**",
        "",
        "> Este reporte no representa cobertura definitiva. Es una fotografía conservadora "
        "del motor actual con budgets pequeños.",
        "",
        "## Resumen",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for key, count in sorted(summary.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend([
        "",
        "## Fuentes",
        "",
        "| Código | Estado | Recursos | HTTP | Siguiente acción |",
        "|---|---|---:|---|---|",
    ])
    for row in results:
        codes = ",".join(map(str, row.get("http_codes") or [])) or "-"
        reuse = f" (reusa {row['reused_probe_from']})" if row.get("reused_probe_from") else ""
        lines.append(
            f"| {row['logical_code']} | {row['status']}{reuse} | "
            f"{row['resources_found']} | {codes} | {row['next_action']} |"
        )

    (report_dir / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mapeo masivo conservador de las fuentes lógicas del Prospector Externo."
    )
    parser.add_argument("--inventory", default="config/source_inventory.yaml")
    parser.add_argument("--report-dir", default=".runtime/source_mapping")
    parser.add_argument("--max-requests", type=int, default=5)
    parser.add_argument("--max-runtime", type=int, default=20)
    parser.add_argument("--subprocess-timeout", type=int, default=35)
    parser.add_argument("--rate-limit", type=float, default=1.0)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-urls", type=int, default=40)
    parser.add_argument("--inter-source-delay", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    sources = load_inventory(Path(args.inventory))
    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit debe ser > 0")
        sources = sources[: args.limit]

    print("=" * 72)
    print("PROSPECTOR EXTERNO — MAPEO MASIVO HTTP/HTML BASELINE")
    print("=" * 72)
    print(f"Fuentes lógicas a reportar: {len(sources)}")
    print(f"Budget por entrypoint: max_requests={args.max_requests}, max_runtime={args.max_runtime}s")
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
            base.update({
                "source_id": source["source_id"],
                "logical_code": source["logical_code"],
                "name": source["name"],
                "entrypoint": source["entrypoint"],
                "host_key": source.get("host_key") or canonical_host(source["entrypoint"]),
                "reused_probe_from": cache[key]["logical_code"],
            })
            row = base
            print(
                f"[{idx:02d}/{len(sources):02d}] {source['logical_code']:<20} "
                f"{row['status']:<25} reuse={row['reused_probe_from']}"
            )
        else:
            row = probe_source(
                source,
                python_executable=sys.executable,
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
                f"[{idx:02d}/{len(sources):02d}] {source['logical_code']:<20} "
                f"{row['status']:<25} resources={row['resources_found']:<4} "
                f"http={row['http_codes']} t={row['elapsed_seconds']}s"
            )
            if args.inter_source_delay > 0 and idx < len(sources):
                time.sleep(args.inter_source_delay)

        results.append(row)

    report_dir = Path(args.report_dir)
    write_reports(results, report_dir, physical_probes=physical_probes)

    summary = Counter(r["status"] for r in results)
    print()
    print("=" * 72)
    print("MAPEO COMPLETADO")
    print("=" * 72)
    print(f"Fuentes lógicas:       {len(results)}")
    print(f"Entrypoints probados:  {physical_probes}")
    for key, count in sorted(summary.items()):
        print(f"{key:<28} {count}")
    print()
    print(f"JSON: {report_dir / 'latest.json'}")
    print(f"CSV:  {report_dir / 'latest.csv'}")
    print(f"MD:   {report_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
