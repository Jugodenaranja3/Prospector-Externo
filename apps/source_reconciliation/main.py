from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from apps.source_mapping.main import inspect_probe_output, parse_checkpoint_resources


RETRY_ACTION = "RETRY_CRAWL"


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def allowed_hosts(url: str) -> list[str]:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return []
    base = host[4:] if host.startswith("www.") else host
    return list(dict.fromkeys([host, base, f"www.{base}"]))


def build_probe(row: dict[str, Any], max_requests: int, max_runtime: int) -> dict[str, Any]:
    entrypoint = row["candidate_entrypoint"]
    return {
        "sources": [
            {
                "source_id": row["source_id"],
                "name": row["logical_code"],
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
                "rate_limit_seconds": 1.0,
                "allowed_hosts": allowed_hosts(entrypoint),
                "max_depth": 2,
                "max_urls": 180,
                "max_runtime_seconds": max_runtime,
                "max_requests": max_requests,
                "max_query_variants": 6,
                "max_consecutive_errors": 3,
                "max_redirects": 6,
                "max_calendar_variants": 6,
                "max_url_length": 2048,
                "max_query_keys": 10,
                "pagination_min_pages": 2,
                "pagination_empty_streak": 2,
                "pagination_window": 4,
                "discover_sitemaps": True,
                "max_sitemap_documents": 4,
                "max_sitemap_urls": 250,
                "max_sitemap_bytes": 750000,
                "discover_apis": True,
                "max_api_endpoints": 12,
                "max_api_response_bytes": 750000,
                "follow_api_pagination": True,
                "max_api_pages": 2,
                "max_api_records_sampled": 50,
                "max_api_stagnant_pages": 1,
                "probe_api_documentation": True,
                "max_api_documents": 4,
                "max_api_document_depth": 1,
                "max_api_document_bytes": 750000,
            }
        ]
    }


def resource_count(crawl_dir: Path, log_text: str) -> int:
    inspected = inspect_probe_output(crawl_dir)
    return max(
        int(inspected.get("resources_found") or 0),
        int(parse_checkpoint_resources(log_text) or 0),
    )


def classify(returncode: int, resources: int, log_text: str) -> str:
    lower = log_text.lower()
    if resources > 0:
        return "RECOVERED_RESOURCES"
    if returncode == 0:
        return "REACHABLE_NO_RESOURCES"
    if "403" in lower or "forbidden" in lower:
        return "ACCESS_RESTRICTED"
    if "certificate" in lower or "ssl" in lower or "tls" in lower:
        return "TLS_FAILURE"
    if "getaddrinfo" in lower or "name resolution" in lower:
        return "DNS_FAILURE"
    if "timeout" in lower:
        return "TIMEOUT"
    return "EXECUTION_ERROR"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reintento dirigido sobre entrypoints reconciliados B6F."
    )
    parser.add_argument(
        "--reconciliation",
        default="config/source_endpoint_reconciliation.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/source_reconciliation",
    )
    parser.add_argument("--max-requests", type=int, default=18)
    parser.add_argument("--max-runtime", type=int, default=55)
    parser.add_argument("--subprocess-timeout", type=int, default=75)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = load_yaml(Path(args.reconciliation))
    rows = [
        row for row in config.get("sources", [])
        if row.get("action") == RETRY_ACTION and row.get("candidate_entrypoint")
    ]

    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit debe ser > 0")
        rows = rows[: args.limit]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []

    print("=" * 78)
    print("B6F — RECONCILED ENDPOINT RETRY")
    print("=" * 78)
    print(f"Fuentes a reintentar: {len(rows)}")
    print("Sitemap/API: SÍ")
    print("Browser: NO")
    print("Binarios: NO")
    print()

    for index, row in enumerate(rows, 1):
        source_dir = output_dir / "sources" / row["source_id"]
        result_path = source_dir / "result.json"

        if result_path.exists() and not args.force:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["resumed"] = True
            results.append(result)
            print(
                f"[{index:02d}/{len(rows):02d}] {row['logical_code']:<20} "
                f"{result['status']:<24} resources={result['resources_found']:<4} (resume)"
            )
            continue

        if source_dir.exists():
            shutil.rmtree(source_dir)
        source_dir.mkdir(parents=True, exist_ok=True)

        probe_path = source_dir / "probe.yaml"
        crawl_dir = source_dir / "crawl"
        log_path = source_dir / "crawl.log"

        probe_path.write_text(
            yaml.safe_dump(
                build_probe(row, args.max_requests, args.max_runtime),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        cmd = [
            sys.executable,
            "-m",
            "apps.crawler_batch.main",
            "--config",
            str(probe_path),
            "--source",
            row["source_id"],
            "--output-dir",
            str(crawl_dir),
            "--force",
        ]

        started = time.monotonic()
        try:
            process = subprocess.run(
                cmd,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=args.subprocess_timeout,
            )
            returncode = process.returncode
            log_text = (process.stdout or "") + "\n" + (process.stderr or "")
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            log_text = out + "\n" + err + "\nSUBPROCESS_TIMEOUT"

        log_path.write_text(log_text, encoding="utf-8")
        resources = resource_count(crawl_dir, log_text)
        status = classify(returncode, resources, log_text)

        result = {
            "source_id": row["source_id"],
            "logical_code": row["logical_code"],
            "historical_entrypoint": row.get("historical_entrypoint"),
            "candidate_entrypoint": row["candidate_entrypoint"],
            "lifecycle": row.get("lifecycle"),
            "status": status,
            "resources_found": resources,
            "returncode": returncode,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "probe_file": str(probe_path),
            "crawl_output_dir": str(crawl_dir),
            "log_file": str(log_path),
            "resumed": False,
        }
        result_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        results.append(result)

        print(
            f"[{index:02d}/{len(rows):02d}] {row['logical_code']:<20} "
            f"{status:<24} resources={resources:<4} "
            f"t={result['elapsed_seconds']}s"
        )

    counts = Counter(row["status"] for row in results)
    payload = {
        "schema_version": "source-reconciliation-run-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "targets": len(results),
        "summary_by_status": dict(sorted(counts.items())),
        "results": results,
    }
    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_dir / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fields = [
            "logical_code", "source_id", "historical_entrypoint",
            "candidate_entrypoint", "lifecycle", "status", "resources_found",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B6F — Reconciled endpoint retry",
        "",
        f"- Fuentes probadas: **{len(results)}**",
        "",
        "| Fuente | Estado | Recursos | Endpoint candidato |",
        "|---|---|---:|---|",
    ]
    for row in results:
        lines.append(
            f"| {row['logical_code']} | {row['status']} | "
            f"{row['resources_found']} | {row['candidate_entrypoint']} |"
        )
    (output_dir / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    print("=" * 78)
    print("B6F COMPLETADO")
    print("=" * 78)
    for key, count in sorted(counts.items()):
        print(f"{key:<28} {count}")
    print()
    print(f"JSON: {output_dir / 'latest.json'}")
    print(f"CSV:  {output_dir / 'latest.csv'}")
    print(f"MD:   {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
