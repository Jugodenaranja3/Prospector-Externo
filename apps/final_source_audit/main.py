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
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"YAML inválido: {path}")
    return value


def index_rows(
    rows: Any,
    key: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"Se esperaba lista para índice {key}")

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"Fila no mapping en índice {key}")
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Fila sin {key}")
        if value in result:
            raise ValueError(f"{key} duplicado: {value}")
        result[value] = row

    return result


def compatibility_index(
    config: dict[str, Any],
) -> dict[str, set[str]]:
    raw = config.get("logical_to_physical")
    if not isinstance(raw, dict):
        raise ValueError(
            "workflow_compatibility.yaml sin logical_to_physical"
        )

    result: dict[str, set[str]] = {}
    for logical, physical_values in raw.items():
        if not isinstance(logical, str) or not logical:
            raise ValueError("Logical workflow inválido")
        if not isinstance(physical_values, list) or not physical_values:
            raise ValueError(
                f"{logical}: compatibilidad física vacía"
            )
        normalized = {
            str(value).strip()
            for value in physical_values
            if str(value).strip()
        }
        if not normalized:
            raise ValueError(
                f"{logical}: compatibilidad física vacía"
            )
        result[logical] = normalized

    return result


def classify_workflow_pair(
    logical: str,
    physical: str,
    compatibility: dict[str, set[str]],
) -> str:
    if logical == physical:
        return "EXACT"

    allowed = compatibility.get(logical, set())
    if physical in allowed:
        return "COMPATIBLE_SPECIALIZATION"

    return "INCOMPATIBLE"


def validate_workflow_runtime(
    repo_root: Path,
    workflows: set[str],
) -> dict[str, Any]:
    source_root = repo_root / "src" / "prospector_externo"
    if not source_root.exists():
        raise ValueError("Falta src/prospector_externo")

    files: list[tuple[Path, str]] = []
    for path in source_root.rglob("*.py"):
        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        files.append((path, text))

    class_alias = {
        "html": "HtmlWorkflow",
        "javascript": "JavascriptWorkflow",
        "api": "ApiWorkflow",
        "custom": "CustomWorkflow",
        "commented_html": "CommentedHtmlWorkflow",
    }

    results: dict[str, dict[str, Any]] = {}

    for workflow in sorted(workflows):
        literal_tokens = (
            f'"{workflow}"',
            f"'{workflow}'",
        )
        class_name = class_alias.get(workflow)

        literal_files: list[str] = []
        class_files: list[str] = []
        module_files: list[str] = []

        for path, text in files:
            rel = str(
                path.relative_to(repo_root)
            ).replace("\\", "/")

            if any(token in text for token in literal_tokens):
                literal_files.append(rel)

            if class_name and class_name in text:
                class_files.append(rel)

            if path.stem.casefold() == workflow.casefold():
                module_files.append(rel)

        # Strict enough to prevent a config-only workflow typo while
        # supporting implementations that expose either a workflow class
        # or a dedicated workflow module.
        supported = bool(literal_files) and bool(
            class_files or module_files
        )

        results[workflow] = {
            "supported_by_static_dispatch_evidence": supported,
            "literal_files": literal_files[:20],
            "class_files": class_files[:20],
            "module_files": module_files[:20],
        }

    return results


def build_final_matrix(
    plan: dict[str, Any],
    execution_map: dict[str, Any],
    sources_cfg: dict[str, Any],
    compatibility_cfg: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    plan_rows = plan.get("sources")
    if not isinstance(plan_rows, list) or len(plan_rows) != 52:
        raise ValueError(
            "Plan operacional debe contener 52 fuentes"
        )

    plan_index = index_rows(
        plan_rows,
        "source_id",
    )

    map_rows = execution_map.get("sources")
    if not isinstance(map_rows, list):
        raise ValueError("Execution map sin sources")
    map_index = index_rows(
        map_rows,
        "logical_source_id",
    )

    physical_rows = sources_cfg.get("sources")
    if not isinstance(physical_rows, list):
        raise ValueError("sources.yaml sin sources:list")
    physical_index = index_rows(
        physical_rows,
        "source_id",
    )

    compatibility = compatibility_index(
        compatibility_cfg
    )

    matrix: list[dict[str, Any]] = []
    workflow_pairs: list[dict[str, Any]] = []

    for source_id, row in plan_index.items():
        next_phase = row.get("next_phase")
        logical_workflow = row.get("workflow_strategy")
        operational_status = row.get(
            "operational_status"
        )

        if next_phase == "OPERATIONAL_CONFIG":
            mapping = map_index.get(source_id)
            if mapping is None:
                raise ValueError(
                    f"{source_id}: operacional sin "
                    "source_execution_map"
                )

            config_source_id = mapping.get(
                "config_source_id"
            )
            if (
                not isinstance(config_source_id, str)
                or not config_source_id
            ):
                raise ValueError(
                    f"{source_id}: mapping sin "
                    "config_source_id"
                )

            physical = physical_index.get(
                config_source_id
            )
            if physical is None:
                raise ValueError(
                    f"{source_id}: config físico "
                    f"{config_source_id!r} no existe"
                )

            physical_workflow = physical.get(
                "workflow"
            )
            if (
                not isinstance(logical_workflow, str)
                or not logical_workflow
            ):
                raise ValueError(
                    f"{source_id}: workflow lógico inválido"
                )
            if (
                not isinstance(physical_workflow, str)
                or not physical_workflow
            ):
                raise ValueError(
                    f"{source_id}: workflow físico inválido"
                )

            relation = classify_workflow_pair(
                logical_workflow,
                physical_workflow,
                compatibility,
            )

            workflow_pairs.append(
                {
                    "source_id": source_id,
                    "logical_code": row.get(
                        "logical_code"
                    ),
                    "logical_workflow": (
                        logical_workflow
                    ),
                    "physical_workflow": (
                        physical_workflow
                    ),
                    "relation": relation,
                    "config_source_id": (
                        config_source_id
                    ),
                }
            )

            matrix.append(
                {
                    "source_id": source_id,
                    "logical_code": row.get(
                        "logical_code"
                    ),
                    "name": row.get("name"),
                    "effective_entrypoint": (
                        row.get(
                            "effective_entrypoint"
                        )
                    ),
                    "final_status": (
                        operational_status
                    ),
                    "planned_workflow_strategy": (
                        logical_workflow
                    ),
                    "execution_workflow": (
                        physical_workflow
                    ),
                    "workflow_relation": relation,
                    "next_phase": "LIVE_AUDIT",
                    "config_source_id": (
                        config_source_id
                    ),
                    "physical_entrypoint": (
                        physical.get("entrypoint")
                    ),
                    "execution_state": (
                        "READY_FOR_B10_LIVE"
                    ),
                    "decision_note": (
                        row.get("decision_note")
                    ),
                }
            )
            continue

        if next_phase == "B10_STATUS":
            if source_id in map_index:
                raise ValueError(
                    f"{source_id}: status-only no debe "
                    "tener execution mapping"
                )

            matrix.append(
                {
                    "source_id": source_id,
                    "logical_code": row.get(
                        "logical_code"
                    ),
                    "name": row.get("name"),
                    "effective_entrypoint": (
                        row.get(
                            "effective_entrypoint"
                        )
                    ),
                    "final_status": (
                        operational_status
                    ),
                    "planned_workflow_strategy": None,
                    "execution_workflow": None,
                    "workflow_relation": None,
                    "next_phase": "FINAL_STATUS",
                    "config_source_id": None,
                    "physical_entrypoint": None,
                    "execution_state": (
                        "NOT_EXECUTED_BY_POLICY"
                    ),
                    "decision_note": (
                        row.get("decision_note")
                    ),
                }
            )
            continue

        raise ValueError(
            f"{source_id}: next_phase no final "
            f"antes de B10: {next_phase!r}"
        )

    incompatible = [
        row
        for row in workflow_pairs
        if row["relation"] == "INCOMPATIBLE"
    ]
    if incompatible:
        compact = [
            (
                row["source_id"],
                row["logical_workflow"],
                row["physical_workflow"],
            )
            for row in incompatible
        ]
        raise ValueError(
            "Workflows lógicos/físicos incompatibles: "
            + repr(compact)
        )

    operational = sum(
        1
        for row in matrix
        if row["execution_state"]
        == "READY_FOR_B10_LIVE"
    )
    status_only = sum(
        1
        for row in matrix
        if row["execution_state"]
        == "NOT_EXECUTED_BY_POLICY"
    )

    if operational != 41:
        raise ValueError(
            f"Se esperaban 41 operacionales, hay "
            f"{operational}"
        )
    if status_only != 11:
        raise ValueError(
            f"Se esperaban 11 status-only, hay "
            f"{status_only}"
        )
    if len(physical_index) != 35:
        raise ValueError(
            "Se esperaban 35 configs físicos, hay "
            f"{len(physical_index)}"
        )

    mapped_physical = {
        row["config_source_id"]
        for row in matrix
        if row["config_source_id"] is not None
    }

    unmapped_physical = sorted(
        set(physical_index) - mapped_physical
    )
    if unmapped_physical:
        raise ValueError(
            "Configs físicos sin misión lógica: "
            + ", ".join(unmapped_physical)
        )

    relation_counts = Counter(
        row["relation"]
        for row in workflow_pairs
    )

    counts = {
        "logical_sources": len(matrix),
        "operational_sources": operational,
        "status_only_sources": status_only,
        "physical_config_sources": (
            len(physical_index)
        ),
        "mapped_physical_sources": (
            len(mapped_physical)
        ),
        "workflow_relations": dict(
            sorted(relation_counts.items())
        ),
    }

    return matrix, counts


def validate_closures(
    plan: dict[str, Any],
    persistence: dict[str, Any],
    execution_map: dict[str, Any],
) -> dict[str, Any]:
    if plan.get("b8_closed") is not True:
        raise ValueError(
            "B8 no aparece cerrado en "
            "source_operational_plan"
        )

    if persistence.get("b9_closed") is not True:
        raise ValueError(
            "B9 no aparece cerrado en "
            "persistence_resolution"
        )

    if (
        execution_map.get(
            "logical_operational_sources"
        )
        != 41
    ):
        raise ValueError(
            "Execution map no declara "
            "41 operacionales"
        )

    if (
        execution_map.get(
            "physical_config_sources"
        )
        != 35
    ):
        raise ValueError(
            "Execution map no declara "
            "35 configs físicos"
        )

    phase_counts = (
        plan.get("summary_by_next_phase")
        or {}
    )

    if (
        phase_counts.get(
            "OPERATIONAL_CONFIG"
        )
        != 41
    ):
        raise ValueError(
            "Plan no declara "
            "41 OPERATIONAL_CONFIG"
        )

    if (
        phase_counts.get("B10_STATUS")
        != 11
    ):
        raise ValueError(
            "Plan no declara 11 B10_STATUS"
        )

    return {
        "b8_closed": True,
        "b9_closed": True,
        "operational_config": 41,
        "b10_status": 11,
    }


def write_outputs(
    matrix: list[dict[str, Any]],
    summary: dict[str, Any],
    output_matrix: Path,
    output_dir: Path,
) -> None:
    output_matrix.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    matrix_yaml = {
        "schema_version": (
            "final-source-matrix-1.1"
        ),
        "generated_at_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "logical_sources": len(matrix),
        "sources": matrix,
    }

    output_matrix.write_text(
        yaml.safe_dump(
            matrix_yaml,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )

    payload = {
        "schema_version": (
            "b10-final-audit-1.1"
        ),
        "generated_at_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        **summary,
        "sources": matrix,
    }

    (output_dir / "latest.json").write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fields = [
        "logical_code",
        "source_id",
        "effective_entrypoint",
        "final_status",
        "planned_workflow_strategy",
        "execution_workflow",
        "workflow_relation",
        "config_source_id",
        "physical_entrypoint",
        "execution_state",
    ]

    with (output_dir / "latest.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
        )
        writer.writeheader()
        for row in matrix:
            writer.writerow(
                {
                    field: row.get(field)
                    for field in fields
                }
            )

    status_counts = Counter(
        str(row.get("final_status") or "UNKNOWN")
        for row in matrix
    )
    execution_workflows = Counter(
        str(
            row.get("execution_workflow")
            or "UNRESOLVED"
        )
        for row in matrix
    )

    lines = [
        "# B10A — Final 52 Source Audit",
        "",
        (
            "- Logical sources: "
            f"**{summary['counts']['logical_sources']}**"
        ),
        (
            "- Operational live audit: "
            f"**{summary['counts']['operational_sources']}**"
        ),
        (
            "- Final status only: "
            f"**{summary['counts']['status_only_sources']}**"
        ),
        (
            "- Physical configs: "
            f"**{summary['counts']['physical_config_sources']}**"
        ),
        "",
        "## Workflow compatibility",
        "",
        "| Relation | Count |",
        "|---|---:|",
    ]

    for key, count in summary["counts"][
        "workflow_relations"
    ].items():
        lines.append(
            f"| {key} | {count} |"
        )

    lines.extend(
        [
            "",
            "## Final status counts",
            "",
            "| Status | Count |",
            "|---|---:|",
        ]
    )

    for key, count in sorted(
        status_counts.items()
    ):
        lines.append(
            f"| {key} | {count} |"
        )

    lines.extend(
        [
            "",
            "## Execution workflow counts",
            "",
            "| Workflow | Count |",
            "|---|---:|",
        ]
    )

    for key, count in sorted(
        execution_workflows.items()
    ):
        lines.append(
            f"| {key} | {count} |"
        )

    lines.extend(
        [
            "",
            "## Runtime workflow dispatch evidence",
            "",
            (
                "| Workflow | "
                "Static dispatch evidence |"
            ),
            "|---|---|",
        ]
    )

    for workflow, evidence in summary[
        "workflow_runtime"
    ].items():
        lines.append(
            f"| {workflow} | "
            f"{evidence['supported_by_static_dispatch_evidence']} |"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "B10A: audit estructural final "
            "52/52 antes del crawl live."
        )
    )

    parser.add_argument(
        "--plan",
        default=(
            "config/source_operational_plan.yaml"
        ),
    )
    parser.add_argument(
        "--execution-map",
        default=(
            "config/source_execution_map.yaml"
        ),
    )
    parser.add_argument(
        "--sources-config",
        default="config/sources.yaml",
    )
    parser.add_argument(
        "--persistence-resolution",
        default=(
            "config/persistence_resolution.yaml"
        ),
    )
    parser.add_argument(
        "--workflow-compatibility",
        default=(
            "config/workflow_compatibility.yaml"
        ),
    )
    parser.add_argument(
        "--output-matrix",
        default=(
            "config/final_source_matrix.yaml"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/b10_final_audit",
    )

    args = parser.parse_args()

    repo_root = Path.cwd()

    plan = load_yaml(Path(args.plan))
    execution_map = load_yaml(
        Path(args.execution_map)
    )
    sources_cfg = load_yaml(
        Path(args.sources_config)
    )
    persistence = load_yaml(
        Path(args.persistence_resolution)
    )
    compatibility_cfg = load_yaml(
        Path(args.workflow_compatibility)
    )

    closures = validate_closures(
        plan,
        persistence,
        execution_map,
    )

    matrix, counts = build_final_matrix(
        plan,
        execution_map,
        sources_cfg,
        compatibility_cfg,
    )

    physical_workflows = {
        str(row["execution_workflow"])
        for row in matrix
        if row.get("execution_workflow")
    }

    workflow_runtime = (
        validate_workflow_runtime(
            repo_root,
            physical_workflows,
        )
    )

    unsupported = [
        workflow
        for workflow, evidence
        in workflow_runtime.items()
        if not evidence[
            "supported_by_static_dispatch_evidence"
        ]
    ]

    if unsupported:
        raise ValueError(
            "Workflows físicos sin evidencia "
            "estática suficiente de dispatch runtime: "
            + ", ".join(unsupported)
        )

    summary = {
        "b10_structural_audit_passed": True,
        "closures": closures,
        "counts": counts,
        "workflow_runtime": workflow_runtime,
        "live_audit_required": True,
        "live_audit_run_id": "b10-final",
    }

    write_outputs(
        matrix,
        summary,
        Path(args.output_matrix),
        Path(args.output_dir),
    )

    print("=" * 78)
    print(
        "B10A — FINAL 52 SOURCE STRUCTURAL AUDIT"
    )
    print("=" * 78)
    print(
        f"Logical sources:      "
        f"{counts['logical_sources']}"
    )
    print(
        f"Operational:          "
        f"{counts['operational_sources']}"
    )
    print(
        f"Status-only:          "
        f"{counts['status_only_sources']}"
    )
    print(
        f"Physical configs:     "
        f"{counts['physical_config_sources']}"
    )
    print(
        f"Mapped physical:      "
        f"{counts['mapped_physical_sources']}"
    )
    print()

    print("Workflow relations:")
    for key, count in counts[
        "workflow_relations"
    ].items():
        print(f"  {key:<28} {count}")

    print()
    print("Workflow runtime evidence:")
    for workflow, evidence in (
        workflow_runtime.items()
    ):
        print(
            f"  {workflow:<18} "
            f"{evidence['supported_by_static_dispatch_evidence']}"
        )

    print()
    print("B8 closed:            True")
    print("B9 closed:            True")
    print("Structural audit:     PASS")
    print("Live final audit:     REQUIRED")
    print()
    print(
        f"MATRIX: {Path(args.output_matrix)}"
    )
    print(
        f"JSON:   "
        f"{Path(args.output_dir) / 'latest.json'}"
    )
    print(
        f"CSV:    "
        f"{Path(args.output_dir) / 'latest.csv'}"
    )
    print(
        f"MD:     "
        f"{Path(args.output_dir) / 'latest.md'}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
