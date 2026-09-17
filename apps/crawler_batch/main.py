"""
Punto de entrada CLI para la aplicación batch efímera del Prospector Externo.
Ejecuta corridas oportunas, checkpoint incremental por fuente y persistencia del reporte.
"""

import sys
import argparse
import logging
from pathlib import Path

# Asegurar que 'src' y la raíz estén en el PYTHONPATH
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
# Importar workflows para autoregistro en el microkernel
import prospector_externo.workflows


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prospector Externo Batch — Descubrimiento e Inventario de Fuentes Estatales"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(project_root / "config" / "sources.yaml"),
        help="Ruta al archivo YAML de configuración de fuentes"
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="ID específico de fuente a explorar (ej. finrural, bbv). Si no se especifica, evalúa todas."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(project_root / "output"),
        help="Directorio destino para exportaciones y estado local"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fuerza la exploración ignorando la fecha de próxima elegibilidad de cadencia"
    )
    parser.add_argument(
        "--backend",
        type=str,
        choices=["json", "mongo"],
        default="json",
        help="Mecanismo de persistencia ('json' local o 'mongo')"
    )
    parser.add_argument(
        "--mongo-uri",
        type=str,
        default="mongodb://localhost:27017",
        help="URI de conexión a MongoDB si se selecciona --backend mongo"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Habilita modo de logs detallado (DEBUG)"
    )

    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger("prospector.batch.main")

    config_path = Path(args.config)
    output_dir = Path(args.output_dir)

    if args.backend == "mongo":
        from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
        logger.info(f"Conectando a persistencia MongoDB en {args.mongo_uri}...")
        repo = MongoPersistenceAdapter(connection_uri=args.mongo_uri)
    else:
        logger.info(f"Usando persistencia local JSON en {output_dir.resolve()}...")
        repo = LocalJsonRepositoryAdapter(base_output_dir=output_dir)

    orchestrator = RunOrchestrator(
        catalog_repo=repo,
        report_repo=repo,
        config_path=config_path
    )

    try:
        report = orchestrator.run_batch(source_id_filter=args.source, force=args.force)
        logger.info(f"Proceso concluido exitosamente. Run ID: {report.run_id}")
    except Exception as e:
        logger.critical(f"Fallo durante la corrida batch: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
