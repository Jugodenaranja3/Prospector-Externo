"""CLI offline: snapshot bruto → DataxProjection → ESTADISTICAS legacy."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.legacy_stats_exporter import LegacyExportPlan
from prospector_externo.application.projection_export_pipeline import DataxProjectionExportPipeline


def _load_plan(path: Path | None) -> LegacyExportPlan | None:
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"Plan legacy no encontrado: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return LegacyExportPlan.model_validate(data or {})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Proyección DATAX offline desde snapshots del Prospector Externo"
    )
    parser.add_argument(
        "--output-dir",
        default=str(project_root / "output"),
        help="Directorio que contiene state/ y snapshots del crawler.",
    )
    parser.add_argument("--source", required=True, help="source_id ya persistido.")
    parser.add_argument(
        "--legacy-plan",
        default=None,
        help="JSON/YAML opcional con LegacyExportPlan para reproducir rutas históricas.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger("prospector.datax_projection")

    output_dir = Path(args.output_dir)
    try:
        plan = _load_plan(Path(args.legacy_plan) if args.legacy_plan else None)
        repo = LocalJsonRepositoryAdapter(output_dir)
        manifest = DataxProjectionExportPipeline(repo).export(
            args.source,
            output_dir,
            legacy_plan=plan,
        )
    except Exception as exc:
        logger.error("Falló proyección DATAX: %s", exc)
        raise SystemExit(1)

    logger.info(
        "Proyección concluida: source=%s run=%s raw=%d selected=%d families=%d legacy=%d",
        manifest.source_id,
        manifest.run_id,
        manifest.raw_resources,
        manifest.selected_resources,
        manifest.families,
        manifest.legacy_records,
    )
    logger.info("Projection: %s", manifest.projection_relpath)
    logger.info("Legacy: %s", manifest.legacy_relpath)


if __name__ == "__main__":
    main()
