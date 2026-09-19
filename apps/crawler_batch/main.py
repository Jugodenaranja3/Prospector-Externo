"""CLI batch del Prospector Externo."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
import prospector_externo.workflows  # noqa: F401 - autoregistro


def setup_logging(verbose: bool = False) -> None:
    # Evita que un mensaje de excepción con bytes/caracteres extraños genere
    # un segundo UnicodeEncodeError en consolas Windows.
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def report_exit_code(report) -> int:
    """El proceso debe fallar si el reporte contiene fuentes FAILED."""
    return 2 if int(getattr(report, "sources_failed", 0) or 0) > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Prospector Externo Batch")
    parser.add_argument("--config", default=str(project_root / "config" / "sources.yaml"))
    parser.add_argument("--source", default=None)
    parser.add_argument("--output-dir", default=str(project_root / "output"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--backend", choices=["json", "mongo"], default="json")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("prospector.batch.main")

    if args.backend == "mongo":
        from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
        repo = MongoPersistenceAdapter(connection_uri=args.mongo_uri)
    else:
        repo = LocalJsonRepositoryAdapter(base_output_dir=Path(args.output_dir))

    orchestrator = RunOrchestrator(
        catalog_repo=repo,
        report_repo=repo,
        config_path=Path(args.config),
    )

    try:
        report = asyncio.run(
            orchestrator.run_batch_async(
                source_id_filter=args.source,
                force=args.force,
            )
        )
        logger.info("Proceso concluido. Run ID: %s", report.run_id)

        exit_code = report_exit_code(report)
        if exit_code:
            logger.error(
                "La corrida terminó con %d fuente(s) FAILED; "
                "se propaga código de salida %d.",
                report.sources_failed,
                exit_code,
            )
            raise SystemExit(exit_code)

    except Exception as exc:
        logger.critical("Fallo durante la corrida batch: %s", ascii(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
