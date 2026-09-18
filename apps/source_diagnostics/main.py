from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


UNRESOLVED_STATUSES = {
    "ACCESS_RESTRICTED",
    "EXECUTION_ERROR",
    "REACHABLE_NEEDS_DEEPER_REVIEW",
    "ROBOTS_REVIEW",
    "UNAVAILABLE",
}


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


def read_stage_logs(stages: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    seen: set[str] = set()

    for stage in stages:
        log_file = stage.get("log_file")
        if not isinstance(log_file, str) or not log_file or log_file in seen:
            continue
        seen.add(log_file)
        path = Path(log_file)
        if path.exists():
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass

    return "\n".join(chunks)


def extract_http_codes(stages: list[dict[str, Any]]) -> list[int]:
    codes: set[int] = set()
    for stage in stages:
        for value in stage.get("site_http_codes") or []:
            if isinstance(value, int):
                codes.add(value)
    return sorted(codes)


def diagnose(
    characterization_status: str,
    stages: list[dict[str, Any]],
    log_text: str,
) -> tuple[str, str]:
    lower = log_text.lower()
    site_codes = extract_http_codes(stages)

    if characterization_status == "ACCESS_RESTRICTED":
        return "HTTP_ACCESS_RESTRICTED", "ACCESS_REVIEW"

    if characterization_status == "REACHABLE_NEEDS_DEEPER_REVIEW":
        return "NO_RESOURCES_AFTER_HTML_API", "B7_OR_CUSTOM_REVIEW"

    if characterization_status == "ROBOTS_REVIEW":
        return "ROBOTS_POLICY_REVIEW", "ROBOTS_REVIEW"

    if characterization_status == "UNAVAILABLE":
        return "SOURCE_UNAVAILABLE", "SOURCE_URL_REVIEW"

    patterns: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "DNS_OR_NAME_RESOLUTION",
            "SOURCE_URL_REVIEW",
            (
                "name or service not known",
                "nodename nor servname",
                "getaddrinfo failed",
                "name resolution",
                "temporary failure in name resolution",
            ),
        ),
        (
            "SSL_TLS_ERROR",
            "SOURCE_URL_OR_TLS_REVIEW",
            (
                "certificate verify failed",
                "sslerror",
                "ssl error",
                "tls",
                "certificate",
            ),
        ),
        (
            "CONNECTION_REFUSED",
            "SOURCE_AVAILABILITY_REVIEW",
            ("connection refused",),
        ),
        (
            "CONNECT_TIMEOUT",
            "NETWORK_RETRY_OR_SOURCE_REVIEW",
            ("connecttimeout", "connect timeout"),
        ),
        (
            "READ_TIMEOUT",
            "NETWORK_RETRY_OR_SOURCE_REVIEW",
            ("readtimeout", "read timeout"),
        ),
        (
            "REDIRECT_LOOP_OR_LIMIT",
            "ENTRYPOINT_REDIRECT_REVIEW",
            ("too many redirects", "redirect loop"),
        ),
    ]

    for reason, route, tokens in patterns:
        if any(token in lower for token in tokens):
            return reason, route

    if 403 in site_codes:
        return "HTTP_403", "ACCESS_REVIEW"
    if 401 in site_codes:
        return "HTTP_401", "ACCESS_REVIEW"
    if any(code >= 500 for code in site_codes):
        return "HTTP_5XX", "SOURCE_AVAILABILITY_REVIEW"

    if any(
        token in lower
        for token in ("max retries exceeded", "connection error", "connecterror")
    ):
        return "NETWORK_OR_CONNECTION_ERROR", "SOURCE_AVAILABILITY_REVIEW"

    return "UNKNOWN_EXECUTION_ERROR", "MANUAL_DIAGNOSTIC_REVIEW"


def physical_key(row: dict[str, Any]) -> str:
    value = row.get("physical_probe_source_id")
    if isinstance(value, str) and value:
        return value
    return row["source_id"]


def build_diagnostics(
    capabilities: dict[str, Any],
    characterization: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    logical_rows = characterization.get("logical_results")
    if not isinstance(logical_rows, list):
        raise ValueError("characterization report sin logical_results")

    cap_sources = capabilities.get("sources")
    if not isinstance(cap_sources, list):
        raise ValueError("capabilities YAML sin sources")

    cap_index = {row["source_id"]: row for row in cap_sources}
    physical: dict[str, dict[str, Any]] = {}
    logical_diagnostics: list[dict[str, Any]] = []

    for row in logical_rows:
        status = row.get("characterization_status")
        if status not in UNRESOLVED_STATUSES:
            continue

        source_id = row["source_id"]
        cap = cap_index.get(source_id, {})
        stages = row.get("stages") or []
        key = physical_key(row)

        if key not in physical:
            log_text = read_stage_logs(stages)
            reason, route = diagnose(status, stages, log_text)
            physical[key] = {
                "physical_probe_source_id": key,
                "entrypoint": row.get("entrypoint"),
                "host_key": row.get("host_key"),
                "characterization_status": status,
                "diagnostic_reason": reason,
                "recommended_route": route,
                "site_http_codes": extract_http_codes(stages),
                "stage_count": len(stages),
                "stage_statuses": [
                    {
                        "stage": stage.get("stage"),
                        "status": stage.get("status"),
                        "diagnostic_reason": stage.get("diagnostic_reason"),
                    }
                    for stage in stages
                ],
                "logical_source_ids": [],
                "logical_codes": [],
            }

        physical[key]["logical_source_ids"].append(source_id)
        physical[key]["logical_codes"].append(row.get("logical_code"))

        logical_diagnostics.append(
            {
                "source_id": source_id,
                "logical_code": row.get("logical_code"),
                "entrypoint": row.get("entrypoint"),
                "characterization_status": status,
                "diagnostic_reason": physical[key]["diagnostic_reason"],
                "recommended_route": physical[key]["recommended_route"],
                "workflow_hint": cap.get("workflow_hint"),
                "operational_readiness": cap.get("operational_readiness"),
                "physical_probe_source_id": key,
            }
        )

    return list(physical.values()), logical_diagnostics


def write_reports(
    output_dir: Path,
    physical_rows: list[dict[str, Any]],
    logical_rows: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    reason_counts = Counter(row["diagnostic_reason"] for row in physical_rows)
    route_counts = Counter(row["recommended_route"] for row in physical_rows)

    payload = {
        "schema_version": "source-diagnostics-report-1.0",
        "generated_at_utc": generated_at,
        "physical_unresolved": len(physical_rows),
        "logical_unresolved": len(logical_rows),
        "summary_by_reason": dict(sorted(reason_counts.items())),
        "summary_by_route": dict(sorted(route_counts.items())),
        "physical_results": physical_rows,
        "logical_results": logical_rows,
    }
    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fields = [
        "logical_code",
        "source_id",
        "entrypoint",
        "characterization_status",
        "diagnostic_reason",
        "recommended_route",
        "physical_probe_source_id",
    ]
    with (output_dir / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in logical_rows:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B6C — Diagnóstico offline de fuentes no resueltas",
        "",
        f"- Generado UTC: `{generated_at}`",
        f"- EntryPoints físicos no resueltos: **{len(physical_rows)}**",
        f"- Fuentes lógicas no resueltas: **{len(logical_rows)}**",
        "",
        "## Razones",
        "",
        "| Razón | Cantidad |",
        "|---|---:|",
    ]
    for reason, count in sorted(reason_counts.items()):
        lines.append(f"| {reason} | {count} |")

    lines.extend([
        "",
        "## Fuentes físicas",
        "",
        "| Fuente física | Estado | Diagnóstico | Ruta recomendada | Códigos HTTP |",
        "|---|---|---|---|---|",
    ])
    for row in physical_rows:
        codes = ",".join(map(str, row["site_http_codes"])) or "-"
        lines.append(
            f"| {row['physical_probe_source_id']} | "
            f"{row['characterization_status']} | "
            f"{row['diagnostic_reason']} | "
            f"{row['recommended_route']} | {codes} |"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico offline de fuentes no resueltas tras B6B."
    )
    parser.add_argument(
        "--capabilities",
        default="config/source_capabilities.yaml",
    )
    parser.add_argument(
        "--characterization-report",
        default=".runtime/source_characterization/latest.json",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/source_diagnostics",
    )
    args = parser.parse_args()

    capabilities = load_yaml(Path(args.capabilities))
    characterization = load_json(Path(args.characterization_report))

    physical_rows, logical_rows = build_diagnostics(
        capabilities,
        characterization,
    )
    write_reports(Path(args.output_dir), physical_rows, logical_rows)

    reasons = Counter(row["diagnostic_reason"] for row in physical_rows)

    print("=" * 76)
    print("B6C — DIAGNÓSTICO OFFLINE DE FUENTES NO RESUELTAS")
    print("=" * 76)
    print(f"Entrypoints físicos no resueltos: {len(physical_rows)}")
    print(f"Fuentes lógicas no resueltas:    {len(logical_rows)}")
    print()
    for reason, count in sorted(reasons.items()):
        print(f"{reason:<36} {count}")
    print()
    print(f"JSON: {Path(args.output_dir) / 'latest.json'}")
    print(f"CSV:  {Path(args.output_dir) / 'latest.csv'}")
    print(f"MD:   {Path(args.output_dir) / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
