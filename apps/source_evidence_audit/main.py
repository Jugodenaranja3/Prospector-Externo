from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


RESOURCE_URL_KEYS = ("normalized_url", "raw_url", "url", "url_descarga", "download_url")
ERROR_KEYS = {
    "error", "errors", "exception", "exceptions", "failure", "failure_reason",
    "reason", "cause", "detail", "message", "last_error", "error_message",
}
DATA_EXTENSIONS = {".csv", ".xlsx", ".xls", ".ods", ".json", ".xml", ".parquet"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".gz"}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON inválido: {path}")
    return data


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _resource_url(item: dict[str, Any]) -> str | None:
    for key in RESOURCE_URL_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extension_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    suffix = Path(path).suffix
    return suffix if len(suffix) <= 10 else ""


def extract_unique_resources(crawl_root: Path) -> list[dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}

    if not crawl_root.exists():
        return []

    for path in crawl_root.rglob("*.json"):
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
                url = _resource_url(item)
                if not url:
                    continue

                record = by_url.setdefault(
                    url,
                    {
                        "url": url,
                        "extension": extension_from_url(url),
                        "titles": set(),
                        "methods": set(),
                        "types": set(),
                        "api": False,
                    },
                )

                for key in ("title", "anchor_text", "descripcion", "name"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        record["titles"].add(value.strip())

                method = item.get("discovery_method")
                if isinstance(method, str) and method:
                    record["methods"].add(method)

                rtype = item.get("resource_type")
                if isinstance(rtype, str) and rtype:
                    record["types"].add(rtype)

                api_value = item.get("api")
                if (
                    api_value not in (None, False, "", [], {})
                    or (isinstance(method, str) and "api" in method.lower())
                    or (isinstance(rtype, str) and "api" in rtype.lower())
                ):
                    record["api"] = True

    result: list[dict[str, Any]] = []
    for url in sorted(by_url):
        record = by_url[url]
        result.append(
            {
                "url": record["url"],
                "extension": record["extension"],
                "titles": sorted(record["titles"]),
                "methods": sorted(record["methods"]),
                "types": sorted(record["types"]),
                "api": bool(record["api"]),
            }
        )
    return result


def classify_api_url(url: str) -> str:
    lower = url.lower()
    path = urlparse(url).path.lower()
    query = urlparse(url).query.lower()

    if "/wp-json/" in lower or "rest_route=" in query or "/wp/v2/" in lower or "oembed" in lower:
        return "CMS_WORDPRESS_API"
    if "/jsonapi/" in lower:
        return "CMS_DRUPAL_API"
    if any(token in lower for token in ("swagger", "openapi", "api-docs")):
        return "API_DOCUMENTATION"
    if any(
        token in lower
        for token in (
            "/dataset", "/datasets", "/data/", "/statistics", "/estadistic",
            "/indicator", "/indicators", "/series", "/query", "/download",
            "/catalog", "/resource", "/resources", "/report", "/reports",
            ".csv", ".xlsx", ".xls", ".ods", ".json", ".xml",
        )
    ):
        return "POSSIBLE_DATA_API"
    if "/api/" in path or path.endswith("/api"):
        return "GENERIC_API"
    return "GENERIC_API"


def audit_ready_candidate(row: dict[str, Any]) -> dict[str, Any]:
    stages = row.get("stages") or []
    winning = row.get("winning_stage")
    stage = next((item for item in stages if item.get("stage") == winning), None)

    if stage is None:
        stage = next(
            (item for item in stages if int(item.get("resources_found") or 0) > 0),
            None,
        )

    if stage is None:
        return {
            "resource_count": 0,
            "extension_counts": {},
            "api_resource_count": 0,
            "api_category_counts": {},
            "api_quality": "NO_RESOURCE_EVIDENCE",
            "file_quality": "NO_RESOURCE_EVIDENCE",
            "sample_resources": [],
        }

    crawl_dir_value = stage.get("crawl_output_dir")
    crawl_dir = Path(crawl_dir_value) if isinstance(crawl_dir_value, str) else Path()
    resources = extract_unique_resources(crawl_dir)

    ext_counts = Counter(item["extension"] or "(none)" for item in resources)
    api_resources = [item for item in resources if item["api"]]
    api_categories = Counter(classify_api_url(item["url"]) for item in api_resources)

    if api_resources:
        if api_categories and set(api_categories) <= {
            "CMS_WORDPRESS_API",
            "CMS_DRUPAL_API",
            "API_DOCUMENTATION",
        }:
            api_quality = "CMS_OR_METADATA_ONLY"
        elif api_categories.get("POSSIBLE_DATA_API", 0) > 0:
            api_quality = "DATA_API_EVIDENCE"
        else:
            api_quality = "GENERIC_API_REVIEW"
    else:
        api_quality = "NO_API_EVIDENCE"

    data_files = sum(ext_counts.get(ext, 0) for ext in DATA_EXTENSIONS)
    docs = sum(ext_counts.get(ext, 0) for ext in DOCUMENT_EXTENSIONS)
    archives = sum(ext_counts.get(ext, 0) for ext in ARCHIVE_EXTENSIONS)

    if data_files > 0:
        file_quality = "STRUCTURED_FILE_EVIDENCE"
    elif docs > 0:
        file_quality = "DOCUMENT_FILE_EVIDENCE"
    elif archives > 0:
        file_quality = "ARCHIVE_EVIDENCE"
    elif resources:
        file_quality = "LINK_OR_API_EVIDENCE"
    else:
        file_quality = "NO_RESOURCE_EVIDENCE"

    return {
        "resource_count": len(resources),
        "extension_counts": dict(sorted(ext_counts.items())),
        "api_resource_count": len(api_resources),
        "api_category_counts": dict(sorted(api_categories.items())),
        "api_quality": api_quality,
        "file_quality": file_quality,
        "sample_resources": [
            {
                "url": item["url"],
                "extension": item["extension"],
                "methods": item["methods"],
                "types": item["types"],
                "api": item["api"],
                "api_category": classify_api_url(item["url"]) if item["api"] else None,
            }
            for item in resources[:20]
        ],
    }


def collect_text_artifacts(stages: list[dict[str, Any]], max_chars_per_file: int = 200_000) -> tuple[str, list[str]]:
    chunks: list[str] = []
    files_read: list[str] = []
    seen: set[str] = set()

    candidate_paths: list[Path] = []
    for stage in stages:
        for key in ("log_file", "crawl_output_dir"):
            value = stage.get(key)
            if not isinstance(value, str) or not value:
                continue
            path = Path(value)
            if path.is_file():
                candidate_paths.append(path)
            elif path.is_dir():
                candidate_paths.extend(path.rglob("*.json"))
                candidate_paths.extend(path.rglob("*.log"))
                candidate_paths.extend(path.rglob("*.txt"))

    for path in candidate_paths:
        key = str(path)
        if key in seen or not path.exists() or not path.is_file():
            continue
        seen.add(key)

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        files_read.append(key)
        chunks.append(text[:max_chars_per_file])

    return "\n".join(chunks), files_read


def extract_error_excerpts(text: str, limit: int = 20) -> list[str]:
    excerpts: list[str] = []
    seen: set[str] = set()

    patterns = [
        r".{0,160}(?:error|exception|failed|failure|timeout|refused|forbidden|ssl|certificate|dns|getaddrinfo|redirect).{0,240}",
        r".{0,160}(?:403|401|500|502|503|504).{0,200}",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            value = " ".join(match.group(0).split())
            if not value or value in seen:
                continue
            seen.add(value)
            excerpts.append(value[:450])
            if len(excerpts) >= limit:
                return excerpts

    return excerpts


def classify_failure(row: dict[str, Any], text: str) -> tuple[str, str]:
    status = row.get("characterization_status")
    stages = row.get("stages") or []
    lower = text.lower()

    site_codes: set[int] = set()
    robots_codes: set[int] = set()
    for stage in stages:
        site_codes.update(code for code in stage.get("site_http_codes") or [] if isinstance(code, int))
        robots_codes.update(code for code in stage.get("robots_http_codes") or [] if isinstance(code, int))

    if status == "ACCESS_RESTRICTED":
        return "HTTP_ACCESS_RESTRICTED", "ACCESS_REVIEW"

    if status == "REACHABLE_NEEDS_DEEPER_REVIEW":
        return "NO_RESOURCES_AFTER_HTML_API", "B7_OR_CUSTOM_REVIEW"

    pattern_groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "DNS_OR_NAME_RESOLUTION",
            "SOURCE_URL_REVIEW",
            (
                "getaddrinfo failed",
                "name or service not known",
                "nodename nor servname",
                "temporary failure in name resolution",
                "name resolution error",
            ),
        ),
        (
            "SSL_TLS_ERROR",
            "TLS_OR_SOURCE_REVIEW",
            (
                "certificate verify failed",
                "sslerror",
                "ssl error",
                "tls error",
                "certificate error",
            ),
        ),
        (
            "CONNECTION_REFUSED",
            "SOURCE_AVAILABILITY_REVIEW",
            ("connection refused",),
        ),
        (
            "CONNECT_TIMEOUT",
            "NETWORK_OR_SOURCE_RETRY",
            ("connecttimeout", "connect timeout"),
        ),
        (
            "READ_TIMEOUT",
            "NETWORK_OR_SOURCE_RETRY",
            ("readtimeout", "read timeout"),
        ),
        (
            "REDIRECT_LOOP_OR_LIMIT",
            "ENTRYPOINT_REDIRECT_REVIEW",
            ("too many redirects", "redirect loop"),
        ),
        (
            "REMOTE_PROTOCOL_ERROR",
            "NETWORK_OR_SERVER_REVIEW",
            ("remoteprotocolerror", "server disconnected", "remote protocol"),
        ),
        (
            "PROXY_ERROR",
            "NETWORK_ENVIRONMENT_REVIEW",
            ("proxyerror", "proxy error"),
        ),
    ]

    for reason, route, tokens in pattern_groups:
        if any(token in lower for token in tokens):
            return reason, route

    if 403 in site_codes:
        return "HTTP_403", "ACCESS_REVIEW"
    if 401 in site_codes:
        return "HTTP_401", "ACCESS_REVIEW"
    if any(code >= 500 for code in site_codes):
        return "SITE_HTTP_5XX", "SOURCE_AVAILABILITY_REVIEW"

    if not site_codes and any(code >= 500 for code in robots_codes):
        return "ROBOTS_HTTP_5XX_BLOCKING", "ROBOTS_OR_SOURCE_REVIEW"

    if not site_codes and robots_codes and all(code in {301, 302, 307, 308} for code in robots_codes):
        return "ROBOTS_REDIRECT_WITHOUT_SITE_CRAWL", "ENTRYPOINT_OR_ROBOTS_REVIEW"

    if not site_codes and not robots_codes:
        return "NO_HTTP_TRACE_EXECUTION_FAILURE", "SOURCE_URL_OR_NETWORK_REVIEW"

    return "UNCLASSIFIED_EXECUTION_FAILURE", "MANUAL_DIAGNOSTIC_REVIEW"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditoría offline de evidencia B6: calidad de candidatos y forense de pendientes."
    )
    parser.add_argument(
        "--characterization-report",
        default=".runtime/source_characterization/latest.json",
    )
    parser.add_argument(
        "--capabilities",
        default="config/source_capabilities.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/source_evidence_audit",
    )
    args = parser.parse_args()

    report = load_json(Path(args.characterization_report))
    capabilities = load_yaml(Path(args.capabilities))
    cap_index = {
        row["source_id"]: row
        for row in capabilities.get("sources", [])
        if isinstance(row, dict) and isinstance(row.get("source_id"), str)
    }

    logical_rows = report.get("logical_results")
    if not isinstance(logical_rows, list):
        raise ValueError("Reporte sin logical_results")

    ready_results: list[dict[str, Any]] = []
    unresolved_results: list[dict[str, Any]] = []

    physical_seen: set[str] = set()

    for row in logical_rows:
        source_id = row["source_id"]
        cap = cap_index.get(source_id, {})
        readiness = cap.get("operational_readiness")

        if readiness == "READY_CANDIDATE":
            audit = audit_ready_candidate(row)
            ready_results.append(
                {
                    "source_id": source_id,
                    "logical_code": row.get("logical_code"),
                    "entrypoint": row.get("entrypoint"),
                    "characterization_status": row.get("characterization_status"),
                    "workflow_hint": cap.get("workflow_hint"),
                    "winning_stage": row.get("winning_stage"),
                    **audit,
                }
            )
            continue

        physical_id = row.get("physical_probe_source_id") or source_id
        if physical_id in physical_seen:
            continue
        physical_seen.add(physical_id)

        text, files_read = collect_text_artifacts(row.get("stages") or [])
        reason, route = classify_failure(row, text)
        unresolved_results.append(
            {
                "physical_probe_source_id": physical_id,
                "logical_codes": [
                    candidate.get("logical_code")
                    for candidate in logical_rows
                    if (candidate.get("physical_probe_source_id") or candidate.get("source_id")) == physical_id
                    and cap_index.get(candidate.get("source_id"), {}).get("operational_readiness") != "READY_CANDIDATE"
                ],
                "entrypoint": row.get("entrypoint"),
                "characterization_status": row.get("characterization_status"),
                "forensic_reason": reason,
                "recommended_route": route,
                "artifact_files_read": len(files_read),
                "error_excerpts": extract_error_excerpts(text),
                "stage_statuses": [
                    {
                        "stage": stage.get("stage"),
                        "status": stage.get("status"),
                        "diagnostic_reason": stage.get("diagnostic_reason"),
                        "site_http_codes": stage.get("site_http_codes"),
                        "robots_http_codes": stage.get("robots_http_codes"),
                    }
                    for stage in row.get("stages") or []
                ],
            }
        )

    api_quality_counts = Counter(row["api_quality"] for row in ready_results)
    file_quality_counts = Counter(row["file_quality"] for row in ready_results)
    forensic_counts = Counter(row["forensic_reason"] for row in unresolved_results)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": "source-evidence-audit-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ready_candidates": len(ready_results),
        "unresolved_physical": len(unresolved_results),
        "summary_api_quality": dict(sorted(api_quality_counts.items())),
        "summary_file_quality": dict(sorted(file_quality_counts.items())),
        "summary_forensic_reason": dict(sorted(forensic_counts.items())),
        "ready_results": ready_results,
        "unresolved_results": unresolved_results,
    }
    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_dir / "ready_candidates.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fields = [
            "logical_code", "source_id", "workflow_hint", "characterization_status",
            "resource_count", "api_resource_count", "api_quality", "file_quality",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in ready_results:
            writer.writerow({key: row.get(key) for key in fields})

    with (output_dir / "unresolved.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fields = [
            "physical_probe_source_id", "entrypoint", "characterization_status",
            "forensic_reason", "recommended_route", "artifact_files_read",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in unresolved_results:
            writer.writerow({key: row.get(key) for key in fields})

    lines = [
        "# B6D — Auditoría offline de evidencia",
        "",
        f"- READY candidates: **{len(ready_results)}**",
        f"- EntryPoints físicos pendientes: **{len(unresolved_results)}**",
        "",
        "## Calidad API",
        "",
        "| Calidad | Cantidad |",
        "|---|---:|",
    ]
    for key, count in sorted(api_quality_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend([
        "",
        "## Evidencia de archivos",
        "",
        "| Calidad | Cantidad |",
        "|---|---:|",
    ])
    for key, count in sorted(file_quality_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend([
        "",
        "## Diagnóstico forense de pendientes",
        "",
        "| Razón | Cantidad |",
        "|---|---:|",
    ])
    for key, count in sorted(forensic_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend([
        "",
        "## Pendientes físicos",
        "",
        "| Fuente | Estado | Razón | Ruta |",
        "|---|---|---|---|",
    ])
    for row in unresolved_results:
        lines.append(
            f"| {row['physical_probe_source_id']} | "
            f"{row['characterization_status']} | "
            f"{row['forensic_reason']} | "
            f"{row['recommended_route']} |"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print("B6D — AUDITORÍA OFFLINE DE EVIDENCIA")
    print("=" * 78)
    print(f"READY candidates auditadas:      {len(ready_results)}")
    print(f"Entrypoints físicos pendientes:  {len(unresolved_results)}")
    print()
    print("Calidad API:")
    for key, count in sorted(api_quality_counts.items()):
        print(f"  {key:<32} {count}")
    print()
    print("Forense pendientes:")
    for key, count in sorted(forensic_counts.items()):
        print(f"  {key:<32} {count}")
    print()
    print(f"JSON:       {output_dir / 'latest.json'}")
    print(f"READY CSV:  {output_dir / 'ready_candidates.csv'}")
    print(f"UNRES CSV:  {output_dir / 'unresolved.csv'}")
    print(f"MD:         {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
