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


def load_result(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Resultado inválido: {path}")
    return data


def aggregate_results(
    strategies_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    strategies = load_yaml(strategies_path)
    rows = strategies.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError("custom_strategy_candidates.yaml sin sources")

    results: list[dict[str, Any]] = []
    missing: list[str] = []

    for row in rows:
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("Candidata custom sin source_id")

        result_path = output_dir / "sources" / source_id / "result.json"
        if not result_path.exists():
            missing.append(source_id)
            continue

        result = load_result(result_path)
        if result.get("source_id") != source_id:
            raise ValueError(
                f"{source_id}: result.json pertenece a "
                f"{result.get('source_id')!r}"
            )
        results.append(result)

    if missing:
        raise RuntimeError(
            "Faltan resultados por fuente: " + ", ".join(missing)
        )

    status_counts = Counter(
        str(row.get("status") or "UNKNOWN")
        for row in results
    )
    route_counts = Counter(
        str(row.get("recommended_route") or "UNKNOWN")
        for row in results
    )

    payload = {
        "schema_version": "custom-probe-report-1.1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": len(results),
        "summary_by_status": dict(sorted(status_counts.items())),
        "summary_by_route": dict(sorted(route_counts.items())),
        "results": results,
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    with (output_dir / "latest.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as fh:
        fields = [
            "logical_code",
            "source_id",
            "strategy",
            "entrypoint",
            "identity_status",
            "status",
            "recommended_route",
            "http_probe_count",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow(
                {field: row.get(field) for field in fields}
            )

    lines = [
        "# B8B — Custom Strategy Live Probes",
        "",
        f"- Fuentes: **{len(results)}**",
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
            "## Rutas",
            "",
            "| Ruta | Cantidad |",
            "|---|---:|",
        ]
    )
    for key, count in sorted(route_counts.items()):
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Fuentes",
            "",
            "| Fuente | Estrategia | Estado | Ruta |",
            "|---|---|---|---|",
        ]
    )
    for row in results:
        lines.append(
            f"| {row.get('logical_code')} | "
            f"{row.get('strategy')} | "
            f"{row.get('status')} | "
            f"{row.get('recommended_route')} |"
        )

    (output_dir / "latest.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconstruye el resumen B8B desde los result.json por fuente, "
            "sin hacer red."
        )
    )
    parser.add_argument(
        "--strategies",
        default="config/custom_strategy_candidates.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/custom_probe",
    )
    args = parser.parse_args()

    payload = aggregate_results(
        Path(args.strategies),
        Path(args.output_dir),
    )

    print("=" * 78)
    print("B8B — RECOVERED AGGREGATE")
    print("=" * 78)
    print(f"Fuentes recuperadas: {payload['sources']}")
    print()
    print("Estados:")
    for key, count in payload["summary_by_status"].items():
        print(f"  {key:<38} {count}")
    print()
    print("Rutas:")
    for key, count in payload["summary_by_route"].items():
        print(f"  {key:<38} {count}")
    print()
    print(f"JSON: {Path(args.output_dir) / 'latest.json'}")
    print(f"CSV:  {Path(args.output_dir) / 'latest.csv'}")
    print(f"MD:   {Path(args.output_dir) / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
