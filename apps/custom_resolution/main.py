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


def index_rows(rows: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = row.get("source_id")
        if isinstance(source_id, str) and source_id:
            result[source_id] = row
    return result


def confirmed_endpoints(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in row.get("http_results") or []:
        if not isinstance(item, dict) or item.get("skipped"):
            continue
        if not (
            item.get("json_data") is True
            or item.get("resource_kind") == "STRUCTURED_FILE"
        ):
            continue
        url = item.get("final_url") or item.get("url")
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    return urls


def successful_seeds(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in row.get("seed_results") or []:
        if not isinstance(item, dict):
            continue
        code = item.get("status_code")
        if not isinstance(code, int) or not (200 <= code < 400):
            continue
        url = item.get("final_url") or item.get("seed")
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    return urls


def resolve_one(
    candidate: dict[str, Any],
    probe: dict[str, Any],
    policy: dict[str, Any] | None,
    identity: dict[str, Any] | None,
    transtats_probe: dict[str, Any],
    transtats_policy: dict[str, Any],
) -> dict[str, Any]:
    source_id = candidate["source_id"]
    status = probe.get("status")
    base = {
        "source_id": source_id,
        "logical_code": candidate.get("logical_code"),
        "name": candidate.get("name"),
        "b8_probe_status": status,
    }

    if status == "CUSTOM_DATA_ENDPOINT_CONFIRMED":
        endpoints = confirmed_endpoints(probe)
        if not endpoints:
            raise ValueError(f"{source_id}: sin endpoint confirmado")
        return {
            **base,
            "resolution_status": "OPERATIONAL_CUSTOM_DATA_API",
            "workflow_strategy": "custom",
            "next_phase": "OPERATIONAL_CONFIG",
            "effective_entrypoint_override": None,
            "decision_note": (
                "B8 confirmó por GET un endpoint con estructura real de datos; "
                "se conserva como workflow custom."
            ),
            "operational_config": {
                "custom_kind": "data_endpoint",
                "allowed_methods": ["GET", "HEAD"],
                "data_endpoints": endpoints,
            },
        }

    if status == "CUSTOM_NO_DATA_EVIDENCE":
        override = None
        note = (
            "B8 no encontró evidencia pública suficiente de datasets "
            "o recursos descargables."
        )
        if isinstance(identity, dict) and identity.get("status") == "RESOLVED_SAME_INSTITUTION":
            candidate_entrypoint = identity.get("candidate_entrypoint")
            if isinstance(candidate_entrypoint, str) and candidate_entrypoint:
                override = candidate_entrypoint
                note = (
                    "La identidad institucional fue reconciliada, pero el sitio "
                    "actual no expuso evidencia pública suficiente de datos."
                )
        return {
            **base,
            "resolution_status": "NO_PUBLIC_DATA_EVIDENCE",
            "workflow_strategy": None,
            "next_phase": "B10_STATUS",
            "effective_entrypoint_override": override,
            "decision_note": note,
            "operational_config": None,
        }

    if status == "CUSTOM_IDENTITY_UNRESOLVED":
        return {
            **base,
            "resolution_status": "IDENTITY_UNRESOLVED",
            "workflow_strategy": None,
            "next_phase": "B10_STATUS",
            "effective_entrypoint_override": None,
            "decision_note": (
                "No existe una sustitución institucional segura para la "
                "fuente histórica."
            ),
            "operational_config": None,
        }

    if status != "CUSTOM_REQUIRES_FORM_POLICY":
        raise ValueError(f"{source_id}: estado B8B no soportado: {status}")

    if not isinstance(policy, dict):
        raise ValueError(f"{source_id}: requiere resultado B8C")

    policy_status = policy.get("status")

    if policy_status == "RESOLVED_NO_PUBLIC_DATA_SCOPE":
        return {
            **base,
            "b8_policy_status": policy_status,
            "resolution_status": "NO_PUBLIC_DATA_SCOPE",
            "workflow_strategy": None,
            "next_phase": "B10_STATUS",
            "effective_entrypoint_override": None,
            "decision_note": (
                policy.get("rationale")
                or "La superficie pública no corresponde a datos públicos."
            ),
            "operational_config": None,
        }

    if policy_status == "RESOLVED_CURATED_HTTP_SEEDS":
        seeds = successful_seeds(policy)
        if not seeds:
            raise ValueError(f"{source_id}: resolución curated sin seeds exitosos")
        return {
            **base,
            "b8_policy_status": policy_status,
            "resolution_status": "OPERATIONAL_HTTP_HTML_CURATED",
            "workflow_strategy": "html",
            "next_phase": "OPERATIONAL_CONFIG",
            "effective_entrypoint_override": None,
            "decision_note": (
                "B8C confirmó páginas GET públicas específicas con recursos; "
                "se promociona HTML con seeds curados."
            ),
            "operational_config": {
                "custom_kind": "curated_html_seeds",
                "allowed_methods": ["GET", "HEAD"],
                "seed_urls": seeds,
                "discovered_resource_hints": (
                    policy.get("discovered_urls") or []
                )[:200],
            },
        }

    if policy_status == "READ_ONLY_POST_QUERY_CANDIDATES":
        if source_id != "transtats":
            raise ValueError(
                f"{source_id}: READ_ONLY_POST_QUERY_CANDIDATES inesperado"
            )
        if transtats_probe.get("confirmed") is not True:
            raise ValueError("TranStats B8D no confirmó el form-resource")
        return {
            **base,
            "b8_policy_status": policy_status,
            "b8d_status": "TRANSTATS_FORM_RESOURCE_CONFIRMED",
            "resolution_status": "OPERATIONAL_CUSTOM_FORM_RESOURCE",
            "workflow_strategy": "custom",
            "next_phase": "OPERATIONAL_CONFIG",
            "effective_entrypoint_override": None,
            "decision_note": (
                "B8D confirmó por GET que DL_SelectFields representa un "
                "trabajo público de adquisición; discovery no envía POST."
            ),
            "operational_config": {
                "custom_kind": "download_form_acquisition_job",
                "allowed_methods": (
                    transtats_policy.get("allowed_methods")
                    or ["GET", "HEAD"]
                ),
                "submission_policy": transtats_policy.get(
                    "submission_policy"
                ),
                "resource_url_patterns": (
                    transtats_policy.get("resource_url_patterns") or []
                ),
                "evidence_seed_urls": (
                    transtats_policy.get("evidence_seed_urls") or []
                ),
            },
        }

    raise ValueError(f"{source_id}: estado B8C no soportado: {policy_status}")


def build_resolution(
    candidates_cfg: dict[str, Any],
    custom_probe_report: dict[str, Any],
    policy_probe_report: dict[str, Any],
    identity_cfg: dict[str, Any],
    transtats_probe: dict[str, Any],
    transtats_policy: dict[str, Any],
) -> dict[str, Any]:
    candidates = candidates_cfg.get("sources")
    if not isinstance(candidates, list) or len(candidates) != 8:
        raise ValueError("Se esperaban 8 candidatas B8")

    custom_index = index_rows(custom_probe_report.get("results"))
    policy_index = index_rows(policy_probe_report.get("results"))
    identity_index = index_rows(identity_cfg.get("sources"))

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        source_id = candidate["source_id"]
        probe = custom_index.get(source_id)
        if probe is None:
            raise ValueError(f"{source_id}: falta resultado B8B")
        rows.append(
            resolve_one(
                candidate,
                probe,
                policy_index.get(source_id),
                identity_index.get(source_id),
                transtats_probe,
                transtats_policy,
            )
        )

    return {
        "schema_version": "custom-resolution-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources_resolved": len(rows),
        "summary_by_resolution_status": dict(
            sorted(Counter(
                row["resolution_status"] for row in rows
            ).items())
        ),
        "summary_by_next_phase": dict(
            sorted(Counter(
                row["next_phase"] for row in rows
            ).items())
        ),
        "summary_by_workflow": dict(
            sorted(Counter(
                row["workflow_strategy"] or "UNRESOLVED"
                for row in rows
            ).items())
        ),
        "sources": rows,
    }


def apply_resolution(
    plan: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    plan_rows = plan.get("sources")
    if not isinstance(plan_rows, list) or len(plan_rows) != 52:
        raise ValueError("El plan debe contener 52 fuentes")

    resolution_index = index_rows(resolution.get("sources"))
    updated: list[dict[str, Any]] = []
    applied = 0

    for row in plan_rows:
        source_id = row.get("source_id")
        overlay = resolution_index.get(source_id)
        if overlay is None:
            updated.append(dict(row))
            continue

        applied += 1
        new_row = dict(row)

        override = overlay.get("effective_entrypoint_override")
        if isinstance(override, str) and override:
            new_row["effective_entrypoint"] = override

        operational = overlay["next_phase"] == "OPERATIONAL_CONFIG"
        new_row.update(
            {
                "operational_status": overlay["resolution_status"],
                "workflow_strategy": overlay.get("workflow_strategy"),
                "discover_apis": (
                    overlay["resolution_status"]
                    == "OPERATIONAL_CUSTOM_DATA_API"
                ),
                "browser_required": False,
                "custom_required": (
                    operational
                    and overlay.get("workflow_strategy") == "custom"
                ),
                "next_phase": overlay["next_phase"],
                "decision_note": overlay.get("decision_note"),
                "b8_status": overlay.get("b8_probe_status"),
                "b8_route": overlay["next_phase"],
                "operational_config": overlay.get("operational_config"),
            }
        )
        updated.append(new_row)

    if applied != 8:
        raise ValueError(f"Se esperaban 8 overlays B8; se aplicaron {applied}")

    if any(row.get("next_phase") == "B8_CUSTOM" for row in updated):
        raise ValueError("Quedaron fuentes en B8_CUSTOM")

    status_counts = Counter(
        str(row.get("operational_status") or "UNKNOWN")
        for row in updated
    )
    phase_counts = Counter(
        str(row.get("next_phase") or "UNKNOWN")
        for row in updated
    )
    workflow_counts = Counter(
        str(row.get("workflow_strategy") or "UNRESOLVED")
        for row in updated
    )

    if phase_counts.get("OPERATIONAL_CONFIG", 0) != 41:
        raise ValueError(
            "Conteo operacional inesperado: "
            f"{phase_counts.get('OPERATIONAL_CONFIG', 0)}"
        )
    if phase_counts.get("B10_STATUS", 0) != 11:
        raise ValueError(
            "Conteo B10 inesperado: "
            f"{phase_counts.get('B10_STATUS', 0)}"
        )

    preserved = {
        key: value
        for key, value in plan.items()
        if key not in {
            "schema_version",
            "generated_at_utc",
            "summary_by_operational_status",
            "summary_by_next_phase",
            "summary_by_workflow",
            "sources",
        }
    }

    return {
        **preserved,
        "schema_version": "source-operational-plan-2.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "logical_sources": 52,
        "browser_sources_resolved": plan.get(
            "browser_sources_resolved", 11
        ),
        "custom_sources_resolved": 8,
        "b8_closed": True,
        "summary_by_operational_status": dict(
            sorted(status_counts.items())
        ),
        "summary_by_next_phase": dict(
            sorted(phase_counts.items())
        ),
        "summary_by_workflow": dict(
            sorted(workflow_counts.items())
        ),
        "sources": updated,
    }


def write_outputs(
    resolution: dict[str, Any],
    plan: dict[str, Any],
    resolution_path: Path,
    plan_path: Path,
    report_dir: Path,
) -> None:
    resolution_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    resolution_path.write_text(
        yaml.safe_dump(
            resolution,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )
    plan_path.write_text(
        yaml.safe_dump(
            plan,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )
    (report_dir / "latest.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fields = [
        "logical_code",
        "source_id",
        "effective_entrypoint",
        "operational_status",
        "workflow_strategy",
        "custom_required",
        "next_phase",
    ]
    with (report_dir / "latest.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in plan["sources"]:
            writer.writerow(
                {field: row.get(field) for field in fields}
            )

    lines = [
        "# B8E — Cierre Custom",
        "",
        f"- Fuentes: **{plan['logical_sources']}**",
        f"- Custom resueltas: **{plan['custom_sources_resolved']}**",
        f"- Operacionales: **{plan['summary_by_next_phase'].get('OPERATIONAL_CONFIG', 0)}**",
        f"- Estados B10: **{plan['summary_by_next_phase'].get('B10_STATUS', 0)}**",
        "",
        "## Estados",
        "",
        "| Estado | Cantidad |",
        "|---|---:|",
    ]
    for key, count in plan["summary_by_operational_status"].items():
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Workflows",
            "",
            "| Workflow | Cantidad |",
            "|---|---:|",
        ]
    )
    for key, count in plan["summary_by_workflow"].items():
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Siguiente fase",
            "",
            "| Fase | Cantidad |",
            "|---|---:|",
        ]
    )
    for key, count in plan["summary_by_next_phase"].items():
        lines.append(f"| {key} | {count} |")

    (report_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolida B8B+B8C+B8D y cierra B8."
    )
    parser.add_argument("--plan", default="config/source_operational_plan.yaml")
    parser.add_argument(
        "--custom-candidates",
        default="config/custom_strategy_candidates.yaml",
    )
    parser.add_argument(
        "--custom-probe",
        default=".runtime/custom_probe/latest.json",
    )
    parser.add_argument(
        "--policy-probe",
        default=".runtime/custom_policy_probe/latest.json",
    )
    parser.add_argument(
        "--identity-resolution",
        default="config/custom_identity_resolution.yaml",
    )
    parser.add_argument(
        "--transtats-probe",
        default=".runtime/transtats_form_resource_probe/latest.json",
    )
    parser.add_argument(
        "--transtats-policy",
        default="config/transtats_custom_resource_policy.yaml",
    )
    parser.add_argument(
        "--output-resolution",
        default="config/custom_resolution.yaml",
    )
    parser.add_argument(
        "--report-dir",
        default=".runtime/b8_closure",
    )
    args = parser.parse_args()

    plan_path = Path(args.plan)

    resolution = build_resolution(
        load_yaml(Path(args.custom_candidates)),
        load_json(Path(args.custom_probe)),
        load_json(Path(args.policy_probe)),
        load_yaml(Path(args.identity_resolution)),
        load_json(Path(args.transtats_probe)),
        load_yaml(Path(args.transtats_policy)),
    )

    plan = apply_resolution(
        load_yaml(plan_path),
        resolution,
    )

    write_outputs(
        resolution,
        plan,
        Path(args.output_resolution),
        plan_path,
        Path(args.report_dir),
    )

    print("=" * 78)
    print("B8E — CLOSE CUSTOM PHASE")
    print("=" * 78)
    print(f"Custom resueltas:    {resolution['sources_resolved']}")
    print(
        "Operacionales:       "
        f"{plan['summary_by_next_phase'].get('OPERATIONAL_CONFIG', 0)}"
    )
    print(
        "Estados B10:         "
        f"{plan['summary_by_next_phase'].get('B10_STATUS', 0)}"
    )
    print()
    print("Resoluciones B8:")
    for key, count in resolution["summary_by_resolution_status"].items():
        print(f"  {key:<38} {count}")
    print()
    print("Plan 52/52:")
    for key, count in plan["summary_by_operational_status"].items():
        print(f"  {key:<38} {count}")
    print()
    print(f"RESOLUTION: {Path(args.output_resolution)}")
    print(f"PLAN:       {plan_path}")
    print(f"JSON:       {Path(args.report_dir) / 'latest.json'}")
    print(f"CSV:        {Path(args.report_dir) / 'latest.csv'}")
    print(f"MD:         {Path(args.report_dir) / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
