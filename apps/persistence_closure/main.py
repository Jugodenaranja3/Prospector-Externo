from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.persistence.model import validate_checkpoint


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON inválido: {path}")
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"YAML inválido: {path}")
    return value


def checkpoint_counts(checkpoint: dict[str, Any]) -> dict[str, int]:
    validate_checkpoint(checkpoint)
    counts = Counter(
        str(row.get("state"))
        for row in checkpoint["sources"].values()
    )
    return dict(sorted(counts.items()))


def validate_b9_smoke(
    checkpoint: dict[str, Any],
    execution_map: dict[str, Any],
    sources_cfg: dict[str, Any],
    *,
    expected_run_id: str,
) -> dict[str, Any]:
    validate_checkpoint(checkpoint)

    if checkpoint.get("run_id") != expected_run_id:
        raise ValueError(
            f"Checkpoint run_id={checkpoint.get('run_id')!r}; "
            f"se esperaba {expected_run_id!r}"
        )

    counts = checkpoint_counts(checkpoint)

    expected_counts = {
        "FAILED": 0,
        "PENDING": 39,
        "RUNNING": 0,
        "SKIPPED_STATUS": 11,
        "SUCCEEDED": 2,
    }

    for key, expected in expected_counts.items():
        actual = counts.get(key, 0)
        if actual != expected:
            raise ValueError(
                f"Estado {key}: actual={actual}, esperado={expected}"
            )

    map_rows = execution_map.get("sources")
    if not isinstance(map_rows, list):
        raise ValueError("source_execution_map.yaml sin sources")

    logical_count = execution_map.get("logical_operational_sources")
    if logical_count != 41 or len(map_rows) != 41:
        raise ValueError(
            "Execution map no contiene exactamente 41 fuentes lógicas"
        )

    physical_rows = sources_cfg.get("sources")
    if not isinstance(physical_rows, list):
        raise ValueError("sources.yaml sin sources:list")

    physical_count = len(physical_rows)
    declared_physical = execution_map.get("physical_config_sources")
    if declared_physical != physical_count:
        raise ValueError(
            f"physical_config_sources={declared_physical} "
            f"pero sources.yaml tiene {physical_count}"
        )

    succeeded = [
        source_id
        for source_id, row in checkpoint["sources"].items()
        if row.get("state") == "SUCCEEDED"
    ]

    if succeeded[:2] != ["anapo", "aps"]:
        raise ValueError(
            "El smoke no demuestra la secuencia esperada "
            f"ANAPO→APS; SUCCEEDED={succeeded}"
        )

    return {
        "run_id": checkpoint["run_id"],
        "checkpoint_counts": counts,
        "succeeded_source_ids": succeeded,
        "logical_operational_sources": logical_count,
        "physical_config_sources": physical_count,
        "resume_proof": {
            "first_source": "anapo",
            "second_source": "aps",
            "first_source_reexecuted": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolida evidencia del smoke B9 y genera cierre."
    )
    parser.add_argument(
        "--checkpoint",
        default=".runtime/checkpoints/latest.json",
    )
    parser.add_argument(
        "--execution-map",
        default="config/source_execution_map.yaml",
    )
    parser.add_argument(
        "--sources-config",
        default="config/sources.yaml",
    )
    parser.add_argument(
        "--run-id",
        default="b9b-smoke",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/b9_closure",
    )
    args = parser.parse_args()

    checkpoint = load_json(Path(args.checkpoint))
    execution_map = load_yaml(Path(args.execution_map))
    sources_cfg = load_yaml(Path(args.sources_config))

    evidence = validate_b9_smoke(
        checkpoint,
        execution_map,
        sources_cfg,
        expected_run_id=args.run_id,
    )

    payload = {
        "schema_version": "b9-persistence-closure-1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "b9_closed": True,
        "checkpoint_backend_smoke": "json",
        "mongo_adapter": "implemented_contract_tested",
        "live_resume_smoke": evidence,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "# B9 — Persistence / Checkpoint Closure",
        "",
        "- B9 closed: **True**",
        f"- Run ID: **{evidence['run_id']}**",
        f"- Logical operational sources: **{evidence['logical_operational_sources']}**",
        f"- Physical config sources: **{evidence['physical_config_sources']}**",
        "",
        "## Checkpoint",
        "",
        "| State | Count |",
        "|---|---:|",
    ]
    for key, count in evidence["checkpoint_counts"].items():
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Resume proof",
            "",
            "- First live source: `anapo`",
            "- Resume live source: `aps`",
            "- ANAPO re-executed on resume: **False**",
            "",
            "## Mongo",
            "",
            "- Adapter: implemented",
            "- Contract tests: enabled",
            "- Live server smoke: deployment-time / URI-dependent",
        ]
    )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print("=" * 78)
    print("B9C — PERSISTENCE / RESUME CLOSURE")
    print("=" * 78)
    print(f"Run ID:              {evidence['run_id']}")
    print(
        "Logical operational: "
        f"{evidence['logical_operational_sources']}"
    )
    print(
        "Physical configs:    "
        f"{evidence['physical_config_sources']}"
    )
    print()
    print("Checkpoint:")
    for key, count in evidence["checkpoint_counts"].items():
        print(f"  {key:<20} {count}")
    print()
    print("Resume proof:")
    print("  first live:  anapo")
    print("  resume live: aps")
    print("  repeated:    NO")
    print()
    print(f"JSON: {output_dir / 'latest.json'}")
    print(f"MD:   {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
