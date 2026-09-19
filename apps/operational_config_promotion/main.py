from __future__ import annotations

import argparse
import copy
import importlib
import inspect
import json
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def norm_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").casefold()


def source_rows(config: Any) -> list[dict[str, Any]]:
    if not isinstance(config, dict):
        raise ValueError("sources.yaml debe ser mapping")
    rows = config.get("sources")
    if not isinstance(rows, list):
        raise ValueError("sources.yaml debe contener sources:list")
    return rows


def operational_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows = plan.get("sources")
    if not isinstance(rows, list) or len(rows) != 52:
        raise ValueError("source_operational_plan.yaml debe contener 52 fuentes")
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("next_phase") == "OPERATIONAL_CONFIG"
    ]


def logical_identity(row: dict[str, Any]) -> str:
    source_id = row.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("Fuente operacional sin source_id")
    return source_id


def physical_group_key(row: dict[str, Any]) -> str:
    physical = row.get("physical_probe_source_id")
    if isinstance(physical, str) and physical.strip():
        return f"id:{physical.strip()}"

    entrypoint = row.get("effective_entrypoint")
    if isinstance(entrypoint, str) and entrypoint.strip():
        return f"url:{norm_url(entrypoint)}"

    return f"logical:{logical_identity(row)}"


def group_operational(
    rows: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for row in rows:
        grouped.setdefault(physical_group_key(row), []).append(row)
    return list(grouped.items())


def existing_by_source_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        source_id = row.get("source_id")
        if isinstance(source_id, str) and source_id:
            result[source_id.casefold()] = row
    return result


def existing_by_entrypoint(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = row.get("entrypoint")
        if isinstance(value, str) and value.strip():
            result.setdefault(norm_url(value), []).append(row)
    return result


def choose_representative(
    group: list[dict[str, Any]],
    desired_id: str,
) -> dict[str, Any]:
    for row in group:
        if str(row.get("source_id") or "").casefold() == desired_id.casefold():
            return row
    return group[0]


def desired_config_source_id(group: list[dict[str, Any]]) -> str:
    physical_ids = {
        str(row.get("physical_probe_source_id")).strip()
        for row in group
        if isinstance(row.get("physical_probe_source_id"), str)
        and str(row.get("physical_probe_source_id")).strip()
    }
    if len(physical_ids) == 1:
        return next(iter(physical_ids))
    if len(physical_ids) > 1:
        raise ValueError(
            "Grupo físico contiene múltiples physical_probe_source_id: "
            + ", ".join(sorted(physical_ids))
        )
    return logical_identity(group[0])


def group_entrypoint(group: list[dict[str, Any]]) -> str:
    values = {
        str(row.get("effective_entrypoint")).strip()
        for row in group
        if isinstance(row.get("effective_entrypoint"), str)
        and str(row.get("effective_entrypoint")).strip()
    }
    normalized = {norm_url(v) for v in values}
    if len(normalized) != 1:
        raise ValueError(
            "Grupo físico no tiene un único effective_entrypoint: "
            + ", ".join(sorted(values))
        )
    return next(iter(values))


def group_workflow(group: list[dict[str, Any]]) -> str:
    values = {
        str(row.get("workflow_strategy")).strip()
        for row in group
        if isinstance(row.get("workflow_strategy"), str)
        and str(row.get("workflow_strategy")).strip()
    }
    if len(values) != 1:
        raise ValueError(
            "Grupo físico no tiene un único workflow_strategy: "
            + ", ".join(sorted(values))
        )
    return next(iter(values))


def inventory_index(inventory: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(inventory, dict):
        return {}
    rows = inventory.get("sources")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("source_id")).casefold(): row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("source_id"), str)
        and row.get("source_id")
    }


def choose_template(existing: list[dict[str, Any]]) -> dict[str, Any]:
    # FINRURAL is intentionally the generic historical template.
    for row in existing:
        if str(row.get("source_id") or "").casefold() == "finrural":
            return copy.deepcopy(row)

    if not existing:
        raise ValueError("No existe template en config/sources.yaml")
    return copy.deepcopy(existing[0])


def clean_template(template: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "allowed_extensions",
        "excluded_path_keywords",
        "rate_limit_seconds",
        "update_category",
        "max_depth",
        "max_urls",
        "max_requests",
        "max_runtime_seconds",
        "max_redirects",
        "max_url_length",
    }
    return {
        key: copy.deepcopy(value)
        for key, value in template.items()
        if key in keep
    }


def derive_seeds(
    group: list[dict[str, Any]],
    entrypoint: str,
) -> list[str]:
    seeds: list[str] = []

    for row in group:
        operational = row.get("operational_config")
        if not isinstance(operational, dict):
            continue

        for key in (
            "seed_urls",
            "data_endpoints",
            "evidence_seed_urls",
        ):
            values = operational.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, str) and value.strip():
                    normalized = value.strip()
                    if normalized not in seeds:
                        seeds.append(normalized)

    if not seeds:
        seeds.append(entrypoint)

    return seeds


def build_generated_entry(
    *,
    group: list[dict[str, Any]],
    config_source_id: str,
    template: dict[str, Any],
    inventory: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    representative = choose_representative(group, config_source_id)
    entrypoint = group_entrypoint(group)
    workflow = group_workflow(group)

    result = clean_template(template)
    result["source_id"] = config_source_id
    result["name"] = (
        representative.get("name")
        or representative.get("logical_code")
        or config_source_id
    )
    result["entrypoint"] = entrypoint
    result["seeds"] = derive_seeds(group, entrypoint)
    result["workflow"] = workflow

    if any(bool(row.get("discover_apis")) for row in group):
        result["discover_apis"] = True

    # Reuse update_category from source inventory when it exists.
    inv = inventory.get(config_source_id.casefold())
    if inv is None:
        for row in group:
            inv = inventory.get(str(row.get("source_id") or "").casefold())
            if inv is not None:
                break

    if isinstance(inv, dict):
        update_category = inv.get("update_category")
        if update_category is not None:
            result["update_category"] = update_category

    return result


def validate_runtime_source_model(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        module = importlib.import_module(
            "src.prospector_externo.domain.models"
        )
    except Exception as exc:
        return {
            "attempted": False,
            "reason": f"import failed: {type(exc).__name__}: {exc}",
            "validated": 0,
        }

    candidates = []
    for _, cls in inspect.getmembers(module, inspect.isclass):
        model_fields = getattr(cls, "model_fields", None)
        if isinstance(model_fields, dict):
            names = set(model_fields)
            if "source_id" in names and (
                "entrypoint" in names
                or "workflow" in names
            ):
                candidates.append(cls)

    if not candidates:
        return {
            "attempted": False,
            "reason": "no compatible Pydantic source model found",
            "validated": 0,
        }

    errors = []
    validated = 0

    for row in rows:
        accepted = False
        local_errors = []
        for cls in candidates:
            try:
                cls(**row)
                accepted = True
                validated += 1
                break
            except Exception as exc:
                local_errors.append(
                    f"{cls.__name__}: {type(exc).__name__}: {exc}"
                )

        if not accepted:
            errors.append(
                {
                    "source_id": row.get("source_id"),
                    "errors": local_errors,
                }
            )

    return {
        "attempted": True,
        "model_candidates": [cls.__name__ for cls in candidates],
        "validated": validated,
        "errors": errors,
    }


def build_candidate(
    current: dict[str, Any],
    plan: dict[str, Any],
    inventory: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    current_rows = source_rows(current)
    op_rows = operational_rows(plan)
    grouped = group_operational(op_rows)

    by_id = existing_by_source_id(current_rows)
    by_url = existing_by_entrypoint(current_rows)
    inv_index = inventory_index(inventory)
    template = choose_template(current_rows)

    output_rows = copy.deepcopy(current_rows)
    output_ids = {
        str(row.get("source_id")).casefold()
        for row in output_rows
        if isinstance(row.get("source_id"), str)
    }

    map_rows = []
    generated = []
    preserved = []

    for _, group in grouped:
        desired_id = desired_config_source_id(group)
        entrypoint = group_entrypoint(group)

        existing = by_id.get(desired_id.casefold())
        if existing is None:
            url_matches = by_url.get(norm_url(entrypoint), [])
            if len(url_matches) == 1:
                existing = url_matches[0]
            elif len(url_matches) > 1:
                raise ValueError(
                    f"{desired_id}: múltiples configs existentes para {entrypoint}"
                )

        if existing is not None:
            config_source_id = str(existing["source_id"])
            preserved.append(config_source_id)
        else:
            config_source_id = desired_id
            generated_entry = build_generated_entry(
                group=group,
                config_source_id=config_source_id,
                template=template,
                inventory=inv_index,
            )
            if config_source_id.casefold() in output_ids:
                raise ValueError(
                    f"source_id físico duplicado: {config_source_id}"
                )
            output_rows.append(generated_entry)
            output_ids.add(config_source_id.casefold())
            generated.append(config_source_id)

        for logical in group:
            map_rows.append(
                {
                    "logical_source_id": logical_identity(logical),
                    "logical_code": logical.get("logical_code"),
                    "config_source_id": config_source_id,
                    "physical_probe_source_id": (
                        logical.get("physical_probe_source_id")
                        or desired_id
                    ),
                    "workflow_strategy": logical.get("workflow_strategy"),
                    "operational_status": logical.get("operational_status"),
                }
            )

    if len(map_rows) != 41:
        raise ValueError(
            f"Se esperaban 41 mappings lógicos, generados {len(map_rows)}"
        )

    mapped_logical = {
        row["logical_source_id"]
        for row in map_rows
    }
    expected_logical = {
        logical_identity(row)
        for row in op_rows
    }
    if mapped_logical != expected_logical:
        missing = sorted(expected_logical - mapped_logical)
        extra = sorted(mapped_logical - expected_logical)
        raise ValueError(
            f"Execution map incompleto. missing={missing}, extra={extra}"
        )

    candidate = copy.deepcopy(current)
    candidate["sources"] = output_rows

    execution_map = {
        "schema_version": "source-execution-map-1.0",
        "logical_operational_sources": 41,
        "physical_config_sources": len(output_rows),
        "sources": map_rows,
    }

    workflow_counts = Counter(
        str(row.get("workflow_strategy") or "UNRESOLVED")
        for row in op_rows
    )

    report = {
        "logical_operational_sources": len(op_rows),
        "physical_groups": len(grouped),
        "existing_physical_sources_before": len(current_rows),
        "physical_sources_after": len(output_rows),
        "generated_physical_sources": len(generated),
        "preserved_physical_sources": sorted(set(preserved)),
        "generated_source_ids": generated,
        "logical_workflow_counts": dict(sorted(workflow_counts.items())),
    }

    runtime_validation = validate_runtime_source_model(output_rows)
    report["runtime_model_validation"] = runtime_validation

    if runtime_validation.get("attempted") and runtime_validation.get("errors"):
        raise ValueError(
            "Candidate sources.yaml no valida contra el modelo runtime: "
            + json.dumps(
                runtime_validation["errors"][:3],
                ensure_ascii=False,
            )
        )

    return candidate, execution_map, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Promueve el plan operacional B8 a config/sources.yaml físico "
            "y genera el map lógico→físico."
        )
    )
    parser.add_argument(
        "--current-sources",
        default="config/sources.yaml",
    )
    parser.add_argument(
        "--plan",
        default="config/source_operational_plan.yaml",
    )
    parser.add_argument(
        "--inventory",
        default="config/source_inventory.yaml",
    )
    parser.add_argument(
        "--output-sources",
        required=True,
    )
    parser.add_argument(
        "--output-map",
        required=True,
    )
    parser.add_argument(
        "--report",
        required=True,
    )
    args = parser.parse_args()

    current = load_yaml(Path(args.current_sources))
    plan = load_yaml(Path(args.plan))

    inventory_path = Path(args.inventory)
    inventory = (
        load_yaml(inventory_path)
        if inventory_path.exists()
        else {}
    )

    if not isinstance(current, dict):
        raise ValueError("sources.yaml inválido")
    if not isinstance(plan, dict):
        raise ValueError("plan operacional inválido")

    candidate, execution_map, report = build_candidate(
        current,
        plan,
        inventory,
    )

    output_sources = Path(args.output_sources)
    output_map = Path(args.output_map)
    report_path = Path(args.report)

    output_sources.parent.mkdir(parents=True, exist_ok=True)
    output_map.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    output_sources.write_text(
        yaml.safe_dump(
            candidate,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )
    output_map.write_text(
        yaml.safe_dump(
            execution_map,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("=" * 78)
    print("B9B.2 — OPERATIONAL SOURCE CONFIG PROMOTION")
    print("=" * 78)
    print(
        f"Logical operational:       "
        f"{report['logical_operational_sources']}"
    )
    print(
        f"Physical groups:           "
        f"{report['physical_groups']}"
    )
    print(
        f"Existing physical before:  "
        f"{report['existing_physical_sources_before']}"
    )
    print(
        f"Generated physical:        "
        f"{report['generated_physical_sources']}"
    )
    print(
        f"Physical after:            "
        f"{report['physical_sources_after']}"
    )
    print()
    print("Workflows lógicos:")
    for key, count in report["logical_workflow_counts"].items():
        print(f"  {key:<20} {count}")
    print()
    print("Runtime model validation:")
    rv = report["runtime_model_validation"]
    print(f"  attempted: {rv.get('attempted')}")
    print(f"  validated: {rv.get('validated')}")
    if rv.get("reason"):
        print(f"  reason:    {rv.get('reason')}")
    print()
    print(f"SOURCES: {output_sources}")
    print(f"MAP:     {output_map}")
    print(f"REPORT:  {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
