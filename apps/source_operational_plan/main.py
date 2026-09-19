from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


B6_READY_STATUSES = {
    "READY_HTTP_HTML",
    "READY_DATA_API",
    "READY_HTML_WITH_API_DISCOVERY",
}

FINAL_NON_ACTIVE_STATUSES = {
    "ACCESS_RESTRICTED",
    "TLS_TECHNICAL_HOLD",
    "RETIRED_HISTORICAL_SOURCE",
    "ARCHIVE_ONLY",
}


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML inválido: {path}")
    return data


def classify_b6_ready(row: dict[str, Any]) -> tuple[str, str, bool]:
    status = row.get("b6_status")
    workflow = row.get("workflow_strategy")

    if status == "READY_HTTP_HTML":
        return "OPERATIONAL_HTTP_HTML", workflow or "html", False

    if status == "READY_DATA_API":
        return "OPERATIONAL_DATA_API", workflow or "api", True

    if status == "READY_HTML_WITH_API_DISCOVERY":
        return "OPERATIONAL_HTML_API_DISCOVERY", workflow or "html", True

    raise ValueError(f"Estado B6 no promocionable: {status}")


def merge_source(
    source: dict[str, Any],
    browser: dict[str, Any] | None,
) -> dict[str, Any]:
    b6_status = source.get("b6_status")
    b6_route = source.get("b6_route")

    base = {
        "source_id": source["source_id"],
        "logical_code": source.get("logical_code"),
        "name": source.get("name"),
        "historical_entrypoint": source.get("historical_entrypoint"),
        "effective_entrypoint": source.get("effective_entrypoint"),
        "lifecycle": source.get("lifecycle"),
        "physical_probe_source_id": source.get("physical_probe_source_id"),
        "baseline_status": source.get("baseline_status"),
        "characterization_status": source.get("characterization_status"),
        "b6_status": b6_status,
        "b6_route": b6_route,
        "b7_status": browser.get("resolution_status") if browser else None,
        "b7_route": browser.get("route") if browser else None,
        "browser_status": browser.get("browser_status") if browser else None,
        "resource_count": (
            browser.get("direct_downloads")
            if browser is not None
            else source.get("resource_count")
        ),
        "api_quality": source.get("api_quality"),
    }

    if b6_status in B6_READY_STATUSES:
        operational_status, workflow, discover_apis = classify_b6_ready(source)
        return {
            **base,
            "operational_status": operational_status,
            "workflow_strategy": workflow,
            "discover_apis": discover_apis,
            "browser_required": False,
            "custom_required": False,
            "next_phase": "OPERATIONAL_CONFIG",
            "decision_note": source.get("decision_note"),
        }

    if b6_route == "B7_BROWSER_CHARACTERIZATION":
        if browser is None:
            raise ValueError(
                f"{source['source_id']}: faltó resolución B7 para candidata browser"
            )

        if browser.get("route") == "PROMOTE_JAVASCRIPT_WORKFLOW":
            return {
                **base,
                "operational_status": "OPERATIONAL_JAVASCRIPT",
                "workflow_strategy": "javascript",
                "discover_apis": True,
                "browser_required": True,
                "custom_required": False,
                "next_phase": "OPERATIONAL_CONFIG",
                "decision_note": browser.get("decision_note"),
            }

        return {
            **base,
            "operational_status": "B8_CUSTOM_CANDIDATE",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_required": False,
            "custom_required": True,
            "next_phase": "B8_CUSTOM",
            "decision_note": (
                browser.get("decision_note")
                or "B7 no produjo evidencia suficiente para promoción JavaScript."
            ),
        }

    if b6_status == "API_SEMANTIC_REVIEW":
        return {
            **base,
            "operational_status": "B8_CUSTOM_CANDIDATE",
            "workflow_strategy": None,
            "discover_apis": True,
            "browser_required": False,
            "custom_required": True,
            "next_phase": "B8_CUSTOM",
            "decision_note": (
                source.get("decision_note")
                or "API genérica sin evidencia semántica suficiente."
            ),
        }

    if b6_status == "IDENTITY_REVIEW_REQUIRED":
        return {
            **base,
            "operational_status": "B8_CUSTOM_CANDIDATE",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_required": False,
            "custom_required": True,
            "next_phase": "B8_CUSTOM",
            "decision_note": source.get("decision_note"),
        }

    if b6_status == "ACCESS_RESTRICTED":
        return {
            **base,
            "operational_status": "ACCESS_RESTRICTED",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_required": False,
            "custom_required": False,
            "next_phase": "B10_STATUS",
            "decision_note": source.get("decision_note"),
        }

    if b6_status == "TLS_TECHNICAL_HOLD":
        return {
            **base,
            "operational_status": "TLS_TECHNICAL_HOLD",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_required": False,
            "custom_required": False,
            "next_phase": "B10_STATUS",
            "decision_note": source.get("decision_note"),
        }

    if b6_status == "RETIRED_HISTORICAL_SOURCE":
        return {
            **base,
            "operational_status": "RETIRED_HISTORICAL_SOURCE",
            "workflow_strategy": None,
            "discover_apis": False,
            "browser_required": False,
            "custom_required": False,
            "next_phase": "B10_STATUS",
            "decision_note": source.get("decision_note"),
        }

    if b6_status == "ARCHIVE_ONLY":
        return {
            **base,
            "operational_status": "ARCHIVE_ONLY",
            "workflow_strategy": source.get("workflow_strategy"),
            "discover_apis": False,
            "browser_required": False,
            "custom_required": False,
            "next_phase": "B10_STATUS",
            "decision_note": source.get("decision_note"),
        }

    return {
        **base,
        "operational_status": "B8_CUSTOM_CANDIDATE",
        "workflow_strategy": None,
        "discover_apis": False,
        "browser_required": False,
        "custom_required": True,
        "next_phase": "B8_CUSTOM",
        "decision_note": (
            source.get("decision_note")
            or f"Estado no resuelto tras B7: {b6_status}"
        ),
    }


def build_plan(
    source_resolution: dict[str, Any],
    browser_resolution: dict[str, Any],
) -> dict[str, Any]:
    source_rows = source_resolution.get("sources")
    browser_rows = browser_resolution.get("sources")

    if not isinstance(source_rows, list) or len(source_rows) != 52:
        raise ValueError("source_resolution.yaml debe contener 52 fuentes")
    if not isinstance(browser_rows, list) or len(browser_rows) != 11:
        raise ValueError("browser_resolution.yaml debe contener 11 fuentes")

    browser_index = {
        row["source_id"]: row
        for row in browser_rows
        if isinstance(row, dict) and isinstance(row.get("source_id"), str)
    }

    merged: list[dict[str, Any]] = []
    browser_overlay_count = 0

    for source in source_rows:
        browser = browser_index.get(source["source_id"])
        if browser is not None:
            browser_overlay_count += 1
        merged.append(merge_source(source, browser))

    if browser_overlay_count != 11:
        raise ValueError(
            f"Se esperaban 11 overlays B7, se aplicaron {browser_overlay_count}"
        )

    status_counts = Counter(row["operational_status"] for row in merged)
    next_phase_counts = Counter(row["next_phase"] for row in merged)
    workflow_counts = Counter(
        row["workflow_strategy"] or "UNRESOLVED"
        for row in merged
    )

    return {
        "schema_version": "source-operational-plan-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "logical_sources": 52,
        "browser_sources_resolved": 11,
        "summary_by_operational_status": dict(sorted(status_counts.items())),
        "summary_by_next_phase": dict(sorted(next_phase_counts.items())),
        "summary_by_workflow": dict(sorted(workflow_counts.items())),
        "sources": merged,
    }


def build_custom_candidates(plan: dict[str, Any]) -> dict[str, Any]:
    rows = [
        row
        for row in plan["sources"]
        if row.get("next_phase") == "B8_CUSTOM"
    ]

    return {
        "schema_version": "custom-candidates-1.0",
        "generated_from": "config/source_operational_plan.yaml",
        "candidate_count": len(rows),
        "sources": [
            {
                "source_id": row["source_id"],
                "logical_code": row.get("logical_code"),
                "name": row.get("name"),
                "historical_entrypoint": row.get("historical_entrypoint"),
                "effective_entrypoint": row.get("effective_entrypoint"),
                "lifecycle": row.get("lifecycle"),
                "b6_status": row.get("b6_status"),
                "b7_status": row.get("b7_status"),
                "operational_status": row.get("operational_status"),
                "custom_reason": row.get("decision_note"),
            }
            for row in rows
        ],
    }


def write_reports(
    plan: dict[str, Any],
    custom: dict[str, Any],
    output_plan: Path,
    output_custom: Path,
    report_dir: Path,
) -> None:
    output_plan.parent.mkdir(parents=True, exist_ok=True)
    output_custom.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    output_plan.write_text(
        yaml.safe_dump(plan, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    output_custom.write_text(
        yaml.safe_dump(custom, allow_unicode=True, sort_keys=False, width=120),
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
        "browser_required",
        "custom_required",
        "next_phase",
        "b6_status",
        "b7_status",
    ]
    with (report_dir / "latest.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in plan["sources"]:
            writer.writerow({field: row.get(field) for field in fields})

    lines = [
        "# B7D — Cierre Browser / Plan Operacional",
        "",
        f"- Fuentes lógicas: **{plan['logical_sources']}**",
        f"- Fuentes resueltas por B7: **{plan['browser_sources_resolved']}**",
        f"- Candidatas B8 Custom: **{custom['candidate_count']}**",
        "",
        "## Estados operacionales",
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
        description=(
            "Integra B6+B7 en un plan operacional 52/52 y congela candidatos B8."
        )
    )
    parser.add_argument(
        "--source-resolution",
        default="config/source_resolution.yaml",
    )
    parser.add_argument(
        "--browser-resolution",
        default="config/browser_resolution.yaml",
    )
    parser.add_argument(
        "--output-plan",
        default="config/source_operational_plan.yaml",
    )
    parser.add_argument(
        "--output-custom",
        default="config/custom_candidates.yaml",
    )
    parser.add_argument(
        "--report-dir",
        default=".runtime/source_operational_plan",
    )
    args = parser.parse_args()

    plan = build_plan(
        load_yaml(Path(args.source_resolution)),
        load_yaml(Path(args.browser_resolution)),
    )
    custom = build_custom_candidates(plan)

    write_reports(
        plan,
        custom,
        Path(args.output_plan),
        Path(args.output_custom),
        Path(args.report_dir),
    )

    print("=" * 78)
    print("B7D — CLOSE BROWSER PHASE")
    print("=" * 78)
    print(f"Fuentes lógicas:       {plan['logical_sources']}")
    print(f"Overlays B7:           {plan['browser_sources_resolved']}")
    print(f"Candidatas B8 Custom:  {custom['candidate_count']}")
    print()
    print("Estados operacionales:")
    for key, count in plan["summary_by_operational_status"].items():
        print(f"  {key:<38} {count}")
    print()
    print("Workflows:")
    for key, count in plan["summary_by_workflow"].items():
        print(f"  {key:<38} {count}")
    print()
    print("Siguiente fase:")
    for key, count in plan["summary_by_next_phase"].items():
        print(f"  {key:<38} {count}")
    print()
    print(f"PLAN:   {Path(args.output_plan)}")
    print(f"CUSTOM: {Path(args.output_custom)}")
    print(f"JSON:   {Path(args.report_dir) / 'latest.json'}")
    print(f"CSV:    {Path(args.report_dir) / 'latest.csv'}")
    print(f"MD:     {Path(args.report_dir) / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
