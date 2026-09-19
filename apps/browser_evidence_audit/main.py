from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


STRUCTURED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".ods", ".json", ".xml", ".parquet"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".gz"}

ANALYTICS_HOST_TOKENS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "clarity.ms",
    "hotjar.com",
    "facebook.com",
    "connect.facebook.net",
    "sentry.io",
    "cloudflareinsights.com",
    "recaptcha.net",
    "google.com/recaptcha",
)
ANALYTICS_URL_TOKENS = (
    "/collect",
    "/analytics",
    "gtag",
    "pixel",
    "telemetry",
    "beacon",
    "/track",
)

CMS_URL_TOKENS = (
    "/wp-json/",
    "/wp-admin/admin-ajax",
    "rest_route=",
    "/jsonapi/",
    "/graphql",
)

DATA_URL_TOKENS = (
    "/dataset",
    "/datasets",
    "/data/",
    "/statistics",
    "/estadistic",
    "/indicator",
    "/indicators",
    "/series",
    "/query",
    "/download",
    "/resource",
    "/resources",
    "/report",
    "/reports",
    ".csv",
    ".xlsx",
    ".xls",
    ".ods",
    ".parquet",
)

JSON_CONTENT_TYPES = (
    "application/json",
    "application/ld+json",
    "application/problem+json",
)

STRUCTURED_CONTENT_TYPES = (
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument",
    "application/xml",
    "text/xml",
)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON inválido: {path}")
    return data


def extension_from_url(url: str) -> str:
    try:
        suffix = Path(urlparse(url).path.lower()).suffix
    except Exception:
        return ""
    return suffix if len(suffix) <= 12 else ""


def file_kind(url: str) -> str:
    ext = extension_from_url(url)
    if ext in STRUCTURED_EXTENSIONS:
        return "STRUCTURED_FILE"
    if ext in DOCUMENT_EXTENSIONS:
        return "DOCUMENT_FILE"
    if ext in ARCHIVE_EXTENSIONS:
        return "ARCHIVE_FILE"
    return "OTHER_FILE"


def same_origin(url_a: str, url_b: str) -> bool:
    try:
        host_a = (urlparse(url_a).hostname or "").lower().removeprefix("www.")
        host_b = (urlparse(url_b).hostname or "").lower().removeprefix("www.")
    except Exception:
        return False
    return bool(host_a and host_b and host_a == host_b)


def network_category(
    event: dict[str, Any],
    entrypoint: str,
) -> str:
    url = str(event.get("url") or "")
    lower_url = url.lower()
    content_type = str(event.get("content_type") or "").lower()

    host = (urlparse(url).hostname or "").lower()

    if any(token in host for token in ANALYTICS_HOST_TOKENS):
        return "ANALYTICS_TELEMETRY"
    if any(token in lower_url for token in ANALYTICS_URL_TOKENS):
        return "ANALYTICS_TELEMETRY"

    if any(token in lower_url for token in CMS_URL_TOKENS):
        return "CMS_OR_GENERIC_BACKEND"

    ext = extension_from_url(url)
    if ext in STRUCTURED_EXTENSIONS:
        return "DATA_ENDPOINT"

    if any(token in lower_url for token in DATA_URL_TOKENS):
        return "DATA_ENDPOINT"

    if any(token in content_type for token in STRUCTURED_CONTENT_TYPES):
        return "DATA_ENDPOINT"

    if any(token in content_type for token in JSON_CONTENT_TYPES):
        if same_origin(url, entrypoint):
            return "SAME_ORIGIN_JSON"
        return "THIRD_PARTY_JSON"

    if same_origin(url, entrypoint):
        return "SAME_ORIGIN_XHR"
    return "THIRD_PARTY_XHR"


def resolution_for_source(
    result: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    entrypoint = str(result.get("entrypoint") or "")
    direct_urls = [
        str(url) for url in result.get("direct_download_urls") or []
        if isinstance(url, str)
    ]

    file_counts = Counter(file_kind(url) for url in direct_urls)
    event_categories = Counter(
        network_category(event, entrypoint)
        for event in events
        if isinstance(event, dict)
    )

    data_event_urls = [
        str(event.get("url"))
        for event in events
        if isinstance(event, dict)
        and network_category(event, entrypoint) == "DATA_ENDPOINT"
        and isinstance(event.get("url"), str)
    ]
    same_origin_json_urls = [
        str(event.get("url"))
        for event in events
        if isinstance(event, dict)
        and network_category(event, entrypoint) == "SAME_ORIGIN_JSON"
        and isinstance(event.get("url"), str)
    ]

    meaningful_event_count = (
        event_categories.get("DATA_ENDPOINT", 0)
        + event_categories.get("SAME_ORIGIN_JSON", 0)
        + event_categories.get("SAME_ORIGIN_XHR", 0)
    )

    if direct_urls:
        if file_counts.get("STRUCTURED_FILE", 0) > 0:
            status = "READY_JAVASCRIPT_STRUCTURED_FILES"
        else:
            status = "READY_JAVASCRIPT_FILES"
        route = "PROMOTE_JAVASCRIPT_WORKFLOW"
        workflow = "javascript"
        decision_note = (
            "El DOM renderizado expuso enlaces descargables que B6 HTTP/API no observó."
        )
    elif event_categories.get("DATA_ENDPOINT", 0) > 0:
        status = "READY_JAVASCRIPT_DATA_NETWORK"
        route = "PROMOTE_JAVASCRIPT_WORKFLOW"
        workflow = "javascript"
        decision_note = (
            "JavaScript reveló endpoints con señales explícitas de datos."
        )
    elif (
        event_categories.get("SAME_ORIGIN_JSON", 0) > 0
        or event_categories.get("SAME_ORIGIN_XHR", 0) > 0
        or event_categories.get("CMS_OR_GENERIC_BACKEND", 0) > 0
    ):
        status = "JAVASCRIPT_NETWORK_REVIEW"
        route = "B7_NETWORK_SEMANTIC_REVIEW"
        workflow = None
        decision_note = (
            "Browser reveló tráfico de aplicación, pero B7C aún no demuestra que "
            "represente datasets/descargas."
        )
    else:
        status = "B8_CUSTOM_CANDIDATE"
        route = "B8_CUSTOM_REVIEW"
        workflow = None
        decision_note = (
            "Browser no añadió evidencia útil de datos frente a B6; requiere revisión custom."
        )

    return {
        "resolution_status": status,
        "route": route,
        "workflow_strategy": workflow,
        "file_category_counts": dict(sorted(file_counts.items())),
        "network_category_counts": dict(sorted(event_categories.items())),
        "meaningful_network_events": meaningful_event_count,
        "data_endpoint_urls": sorted(set(data_event_urls))[:100],
        "same_origin_json_urls": sorted(set(same_origin_json_urls))[:100],
        "decision_note": decision_note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditoría semántica offline de evidencia browser B7."
    )
    parser.add_argument(
        "--candidates",
        default="config/browser_candidates.yaml",
    )
    parser.add_argument(
        "--browser-report",
        default=".runtime/browser_characterization/latest.json",
    )
    parser.add_argument(
        "--artifacts-root",
        default=".runtime/browser_characterization",
    )
    parser.add_argument(
        "--output-yaml",
        default="config/browser_resolution.yaml",
    )
    parser.add_argument(
        "--report-dir",
        default=".runtime/browser_resolution",
    )
    args = parser.parse_args()

    candidates = load_yaml(Path(args.candidates))
    browser_report = load_json(Path(args.browser_report))

    candidate_rows = candidates.get("sources")
    result_rows = browser_report.get("results")

    if not isinstance(candidate_rows, list):
        raise ValueError("browser_candidates.yaml sin sources")
    if not isinstance(result_rows, list):
        raise ValueError("browser report sin results")

    result_index = {
        row["source_id"]: row
        for row in result_rows
        if isinstance(row, dict) and isinstance(row.get("source_id"), str)
    }

    missing = [
        row["source_id"]
        for row in candidate_rows
        if row.get("source_id") not in result_index
    ]
    if missing:
        raise ValueError(
            "Faltan resultados browser para: " + ", ".join(missing)
        )

    artifacts_root = Path(args.artifacts_root)
    resolved: list[dict[str, Any]] = []

    for candidate in candidate_rows:
        source_id = candidate["source_id"]
        result = result_index[source_id]

        events_path = (
            artifacts_root
            / "sources"
            / source_id
            / "network_events.json"
        )
        if events_path.exists():
            raw_events = json.loads(
                events_path.read_text(encoding="utf-8")
            )
            events = raw_events if isinstance(raw_events, list) else []
        else:
            events = []

        resolution = resolution_for_source(result, events)

        resolved.append(
            {
                "source_id": source_id,
                "logical_code": candidate.get("logical_code"),
                "name": candidate.get("name"),
                "effective_entrypoint": candidate.get("effective_entrypoint"),
                "b6_status": candidate.get("b6_status"),
                "browser_status": result.get("status"),
                "navigation_ok": result.get("navigation_ok"),
                "final_url": result.get("final_url"),
                "rendered_links": int(result.get("rendered_links") or 0),
                "direct_downloads": int(result.get("direct_downloads") or 0),
                "direct_download_urls": result.get("direct_download_urls") or [],
                "xhr_fetch_events": int(result.get("xhr_fetch_events") or 0),
                "data_network_events_original": int(
                    result.get("data_network_events") or 0
                ),
                **resolution,
            }
        )

    status_counts = Counter(
        row["resolution_status"] for row in resolved
    )
    route_counts = Counter(row["route"] for row in resolved)

    payload = {
        "schema_version": "browser-resolution-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_sources": len(resolved),
        "summary_by_status": dict(sorted(status_counts.items())),
        "summary_by_route": dict(sorted(route_counts.items())),
        "sources": resolved,
    }

    output_yaml = Path(args.output_yaml)
    report_dir = Path(args.report_dir)
    output_yaml.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    output_yaml.write_text(
        yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )

    (report_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fields = [
        "logical_code",
        "source_id",
        "browser_status",
        "resolution_status",
        "route",
        "workflow_strategy",
        "rendered_links",
        "direct_downloads",
        "xhr_fetch_events",
        "meaningful_network_events",
    ]
    with (report_dir / "latest.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in resolved:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B7C — Resolución semántica Browser",
        "",
        f"- Fuentes: **{len(resolved)}**",
        "",
        "## Estados",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for key, count in sorted(status_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Fuentes",
            "",
            "| Fuente | Browser | Resolución | Files | XHR/Fetch | Meaningful | Ruta |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    for row in resolved:
        lines.append(
            f"| {row['logical_code']} | "
            f"{row['browser_status']} | "
            f"{row['resolution_status']} | "
            f"{row['direct_downloads']} | "
            f"{row['xhr_fetch_events']} | "
            f"{row['meaningful_network_events']} | "
            f"{row['route']} |"
        )

    (report_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print("B7C — BROWSER EVIDENCE SEMANTIC AUDIT")
    print("=" * 78)
    print(f"Fuentes auditadas: {len(resolved)}")
    print()
    print("Estados:")
    for key, count in sorted(status_counts.items()):
        print(f"  {key:<38} {count}")
    print()
    print("Rutas:")
    for key, count in sorted(route_counts.items()):
        print(f"  {key:<38} {count}")
    print()
    print(f"YAML: {output_yaml}")
    print(f"JSON: {report_dir / 'latest.json'}")
    print(f"CSV:  {report_dir / 'latest.csv'}")
    print(f"MD:   {report_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
