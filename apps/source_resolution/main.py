from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


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


def index_rows(rows: Any, key: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get(key)
        if isinstance(value, str) and value:
            result[value] = row
    return result


def derive_ready_decision(
    source: dict[str, Any],
    audit: dict[str, Any] | None,
) -> dict[str, Any]:
    characterization = source.get("characterization_status")
    workflow_hint = source.get("workflow_hint")
    api_quality = audit.get("api_quality") if audit else None
    file_quality = audit.get("file_quality") if audit else None
    resources = int((audit or {}).get("resource_count") or source.get("max_resources_observed") or 0)
    api_resources = int((audit or {}).get("api_resource_count") or source.get("max_api_resources_observed") or 0)

    if characterization == "HTTP_HTML_CANDIDATE":
        return {
            "b6_status": "READY_HTTP_HTML",
            "b6_route": "PROMOTE_OPERATIONAL_CONFIG",
            "workflow_strategy": "html",
            "discover_apis": False,
            "browser_review": False,
            "custom_review": False,
            "resource_count": resources,
            "api_resource_count": api_resources,
            "api_quality": api_quality,
            "file_quality": file_quality,
            "decision_note": "B6 encontró recursos mediante HTTP/HTML; no requiere browser para discovery básico.",
        }

    if characterization == "API_CANDIDATE":
        if api_quality == "DATA_API_EVIDENCE":
            return {
                "b6_status": "READY_DATA_API",
                "b6_route": "PROMOTE_OPERATIONAL_CONFIG",
                "workflow_strategy": "api",
                "discover_apis": True,
                "browser_review": False,
                "custom_review": False,
                "resource_count": resources,
                "api_resource_count": api_resources,
                "api_quality": api_quality,
                "file_quality": file_quality,
                "decision_note": "Existe evidencia explícita de API orientada a datos.",
            }

        if api_quality == "CMS_OR_METADATA_ONLY":
            return {
                "b6_status": "READY_HTML_WITH_API_DISCOVERY",
                "b6_route": "PROMOTE_OPERATIONAL_CONFIG",
                "workflow_strategy": "html",
                "discover_apis": True,
                "browser_review": False,
                "custom_review": False,
                "resource_count": resources,
                "api_resource_count": api_resources,
                "api_quality": api_quality,
                "file_quality": file_quality,
                "decision_note": (
                    "La capa API encontrada parece CMS/metadatos; se conserva como mecanismo auxiliar "
                    "de discovery y no se promociona a workflow API puro."
                ),
            }

        if api_quality == "GENERIC_API_REVIEW":
            return {
                "b6_status": "API_SEMANTIC_REVIEW",
                "b6_route": "REVIEW_BEFORE_PROMOTION",
                "workflow_strategy": None,
                "discover_apis": True,
                "browser_review": False,
                "custom_review": True,
                "resource_count": resources,
                "api_resource_count": api_resources,
                "api_quality": api_quality,
                "file_quality": file_quality,
                "decision_note": "Existe API genérica pero B6 no demostró todavía que sea una API de datos.",
            }

        return {
            "b6_status": "READY_HTML_WITH_API_DISCOVERY",
            "b6_route": "PROMOTE_OPERATIONAL_CONFIG",
            "workflow_strategy": "html",
            "discover_apis": True,
            "browser_review": False,
            "custom_review": False,
            "resource_count": resources,
            "api_resource_count": api_resources,
            "api_quality": api_quality,
            "file_quality": file_quality,
            "decision_note": "Discovery enriquecido encontró recursos; API no se considera semánticamente primaria.",
        }

    return {
        "b6_status": "READY_REVIEW",
        "b6_route": "REVIEW_BEFORE_PROMOTION",
        "workflow_strategy": workflow_hint,
        "discover_apis": bool(workflow_hint == "api"),
        "browser_review": False,
        "custom_review": True,
        "resource_count": resources,
        "api_resource_count": api_resources,
        "api_quality": api_quality,
        "file_quality": file_quality,
        "decision_note": "Candidato listo requiere revisión semántica adicional.",
    }


def derive_reconciliation_decision(
    source: dict[str, Any],
    recon: dict[str, Any],
    recon_run: dict[str, Any] | None,
) -> dict[str, Any]:
    action = recon.get("action")
    lifecycle = recon.get("lifecycle")
    candidate = recon.get("candidate_entrypoint")
    run_status = (recon_run or {}).get("status")
    resources = int((recon_run or {}).get("resources_found") or 0)

    common = {
        "effective_entrypoint": candidate or source.get("entrypoint"),
        "lifecycle": lifecycle,
        "reconciliation_action": action,
        "reconciliation_run_status": run_status,
        "resource_count": resources,
        "api_resource_count": 0,
        "api_quality": None,
        "file_quality": None,
    }

    if action == "RETRY_CRAWL":
        if run_status == "RECOVERED_RESOURCES":
            return {
                **common,
                "b6_status": "READY_RECONCILED_HTTP_API",
                "b6_route": "PROMOTE_OPERATIONAL_CONFIG",
                "workflow_strategy": "html",
                "discover_apis": True,
                "browser_review": False,
                "custom_review": False,
                "decision_note": "Endpoint reconciliado produjo recursos con HTTP/sitemap/API.",
            }
        if run_status == "REACHABLE_NO_RESOURCES":
            return {
                **common,
                "b6_status": "B7_B8_CANDIDATE",
                "b6_route": "B7_BROWSER_CHARACTERIZATION",
                "workflow_strategy": None,
                "discover_apis": True,
                "browser_review": True,
                "custom_review": True,
                "decision_note": (
                    "Endpoint actual accesible, pero HTTP + sitemap + API discovery no encontraron recursos. "
                    "Pasa a caracterización browser/custom."
                ),
            }
        return {
            **common,
            "b6_status": "RECONCILIATION_TECHNICAL_HOLD",
            "b6_route": "TECHNICAL_REVIEW",
            "workflow_strategy": None,
            "discover_apis": True,
            "browser_review": False,
            "custom_review": True,
            "decision_note": "El endpoint reconciliado no produjo un resultado concluyente.",
        }

    if action == "DEFER_B7_B8":
        return {
            **common,
            "b6_status": "B7_B8_CANDIDATE",
            "b6_route": "B7_BROWSER_CHARACTERIZATION",
            "workflow_strategy": None,
            "discover_apis": True,
            "browser_review": True,
            "custom_review": True,
            "decision_note": "B6 agotó HTML/API sin recursos; pasa a browser/custom review.",
        }

    if action == "HOLD_ACCESS_REVIEW":
        return {
            **common,
            "b6_status": "ACCESS_RESTRICTED",
            "b6_route": "ACCESS_POLICY_REVIEW",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_review": False,
            "custom_review": False,
            "decision_note": "Acceso restringido observado; no intentar bypass.",
        }

    if action == "HOLD_TLS_REVIEW":
        return {
            **common,
            "b6_status": "TLS_TECHNICAL_HOLD",
            "b6_route": "TLS_CLIENT_REVIEW",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_review": False,
            "custom_review": True,
            "decision_note": "La institución sigue vigente pero el cliente actual presenta incompatibilidad TLS.",
        }

    if action == "RETIRED_NO_SUBSTITUTION":
        return {
            **common,
            "b6_status": "RETIRED_HISTORICAL_SOURCE",
            "b6_route": "B10_HISTORICAL_STATUS",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_review": False,
            "custom_review": False,
            "decision_note": (
                "Fuente histórica retirada. El sucesor se conserva como identidad separada; "
                "no sustituir silenciosamente."
            ),
        }

    if action == "ARCHIVE_NO_LIVE_PROMOTION":
        return {
            **common,
            "b6_status": "ARCHIVE_ONLY",
            "b6_route": "B10_ARCHIVE_STATUS",
            "workflow_strategy": "html",
            "discover_apis": False,
            "browser_review": False,
            "custom_review": True,
            "decision_note": "Fuente discontinuada con archivo histórico; no tratar como portal live actual.",
        }

    if action == "NEEDS_IDENTITY_REVIEW":
        return {
            **common,
            "b6_status": "IDENTITY_REVIEW_REQUIRED",
            "b6_route": "B8_IDENTITY_CUSTOM_REVIEW",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_review": False,
            "custom_review": True,
            "decision_note": "Dominio histórico muerto y no existe reemplazo seguro confirmado.",
        }

    return {
        **common,
        "b6_status": "UNRESOLVED_RECONCILIATION",
        "b6_route": "MANUAL_REVIEW",
        "workflow_strategy": None,
        "discover_apis": False,
        "browser_review": False,
        "custom_review": True,
        "decision_note": "Acción de reconciliación no reconocida.",
    }


def build_resolution(
    capabilities: dict[str, Any],
    audit: dict[str, Any],
    reconciliation: dict[str, Any],
    reconciliation_run: dict[str, Any],
) -> dict[str, Any]:
    cap_rows = capabilities.get("sources")
    if not isinstance(cap_rows, list) or len(cap_rows) != 52:
        raise ValueError("source_capabilities debe contener 52 fuentes")

    ready_index = index_rows(audit.get("ready_results"), "source_id")
    recon_index = index_rows(reconciliation.get("sources"), "source_id")
    recon_run_index = index_rows(reconciliation_run.get("results"), "source_id")

    sources: list[dict[str, Any]] = []

    for source in cap_rows:
        source_id = source["source_id"]
        recon = recon_index.get(source_id)

        if recon is not None:
            decision = derive_reconciliation_decision(
                source,
                recon,
                recon_run_index.get(source_id),
            )
        elif source.get("operational_readiness") == "READY_CANDIDATE":
            decision = derive_ready_decision(
                source,
                ready_index.get(source_id),
            )
            decision["effective_entrypoint"] = source.get("entrypoint")
            decision["lifecycle"] = "CURRENT"
            decision["reconciliation_action"] = None
            decision["reconciliation_run_status"] = None
        else:
            decision = {
                "effective_entrypoint": source.get("entrypoint"),
                "lifecycle": "UNRESOLVED",
                "reconciliation_action": None,
                "reconciliation_run_status": None,
                "b6_status": "UNRESOLVED_B6",
                "b6_route": "MANUAL_REVIEW",
                "workflow_strategy": None,
                "discover_apis": False,
                "browser_review": False,
                "custom_review": True,
                "resource_count": int(source.get("max_resources_observed") or 0),
                "api_resource_count": int(source.get("max_api_resources_observed") or 0),
                "api_quality": None,
                "file_quality": None,
                "decision_note": "Fuente no resuelta por evidencia B6 disponible.",
            }

        sources.append(
            {
                "source_id": source_id,
                "logical_code": source.get("logical_code"),
                "name": source.get("name"),
                "historical_entrypoint": source.get("entrypoint"),
                "physical_probe_source_id": source.get("physical_probe_source_id"),
                "baseline_status": source.get("baseline_status"),
                "characterization_status": source.get("characterization_status"),
                **decision,
            }
        )

    status_counts = Counter(row["b6_status"] for row in sources)
    route_counts = Counter(row["b6_route"] for row in sources)

    return {
        "schema_version": "source-resolution-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "logical_sources": len(sources),
        "summary_by_b6_status": dict(sorted(status_counts.items())),
        "summary_by_route": dict(sorted(route_counts.items())),
        "sources": sources,
    }


def write_outputs(
    payload: dict[str, Any],
    output_yaml: Path,
    report_dir: Path,
) -> None:
    output_yaml.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    output_yaml.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    (report_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fields = [
        "logical_code",
        "source_id",
        "historical_entrypoint",
        "effective_entrypoint",
        "lifecycle",
        "b6_status",
        "b6_route",
        "workflow_strategy",
        "discover_apis",
        "browser_review",
        "custom_review",
        "resource_count",
        "api_resource_count",
        "api_quality",
        "file_quality",
    ]
    with (report_dir / "latest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in payload["sources"]:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B6 — Cierre HTTP/API",
        "",
        f"- Fuentes lógicas: **{payload['logical_sources']}**",
        "",
        "## Estados B6",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for key, count in payload["summary_by_b6_status"].items():
        lines.append(f"| {key} | {count} |")

    lines.extend([
        "",
        "## Rutas siguientes",
        "",
        "| Ruta | Cantidad |",
        "|---|---:|",
    ])
    for key, count in payload["summary_by_route"].items():
        lines.append(f"| {key} | {count} |")

    lines.extend([
        "",
        "## Fuentes",
        "",
        "| Código | Estado B6 | Ruta | Workflow | Browser | Endpoint efectivo |",
        "|---|---|---|---|---|---|",
    ])
    for row in payload["sources"]:
        lines.append(
            f"| {row['logical_code']} | {row['b6_status']} | {row['b6_route']} | "
            f"{row.get('workflow_strategy') or '-'} | "
            f"{'sí' if row.get('browser_review') else 'no'} | "
            f"{row.get('effective_entrypoint') or '-'} |"
        )

    (report_dir / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolida la evidencia B6 en una matriz final de resolución 52/52."
    )
    parser.add_argument("--capabilities", default="config/source_capabilities.yaml")
    parser.add_argument("--audit-report", default=".runtime/source_evidence_audit/latest.json")
    parser.add_argument("--reconciliation", default="config/source_endpoint_reconciliation.yaml")
    parser.add_argument("--reconciliation-run", default=".runtime/source_reconciliation/latest.json")
    parser.add_argument("--output-yaml", default="config/source_resolution.yaml")
    parser.add_argument("--report-dir", default=".runtime/source_resolution")
    args = parser.parse_args()

    payload = build_resolution(
        load_yaml(Path(args.capabilities)),
        load_json(Path(args.audit_report)),
        load_yaml(Path(args.reconciliation)),
        load_json(Path(args.reconciliation_run)),
    )

    write_outputs(
        payload,
        Path(args.output_yaml),
        Path(args.report_dir),
    )

    print("=" * 78)
    print("B6G — CIERRE HTTP/API 52/52")
    print("=" * 78)
    print(f"Fuentes lógicas: {payload['logical_sources']}")
    print()
    print("Estados:")
    for key, count in payload["summary_by_b6_status"].items():
        print(f"  {key:<36} {count}")
    print()
    print("Rutas:")
    for key, count in payload["summary_by_route"].items():
        print(f"  {key:<36} {count}")
    print()
    print(f"YAML: {Path(args.output_yaml)}")
    print(f"JSON: {Path(args.report_dir) / 'latest.json'}")
    print(f"CSV:  {Path(args.report_dir) / 'latest.csv'}")
    print(f"MD:   {Path(args.report_dir) / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
