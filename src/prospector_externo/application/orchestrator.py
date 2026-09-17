"""
Orquestador principal de corridas oportunas del Prospector Externo (RunOrchestrator).
Coordina el ciclo de vida completo de la corrida, evaluación de elegibilidad,
despacho a workflows, checkpointing incremental por fuente y persistencia del reporte.
"""

import uuid
import yaml
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict
from concurrent.futures import as_completed

from prospector_externo.domain.models import SourceConfig
from prospector_externo.domain.observations import RunReport, SourceRunObservation, ExecutionStatus, ContentStatus
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort
from prospector_externo.application.status_gate import SourceStatusGate
from prospector_externo.application.dispatcher import SourceDispatcher
from prospector_externo.application.catalog_service import CatalogApplicationService
from prospector_externo.application.cadence_evaluator import CadencePolicyEvaluator
from prospector_externo.application.aggregator import ResultAggregator
from prospector_externo.application.report_builder import RunReportBuilder
from prospector_externo.infrastructure.concurrency import AsyncWorkerPool

logger = logging.getLogger("prospector.application.orchestrator")


class RunOrchestrator:
    """Orquesta la ejecución de una corrida sobre fuentes configuradas."""

    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        report_repo: RunReportRepositoryPort,
        config_path: Optional[Path] = None,
        worker_pool: Optional[AsyncWorkerPool] = None
    ):
        self.catalog_repo = catalog_repo
        self.report_repo = report_repo
        self.config_path = config_path
        self.worker_pool = worker_pool or AsyncWorkerPool(max_http_workers=4, max_browser_workers=2)

        self.status_gate = SourceStatusGate()
        self.dispatcher = SourceDispatcher()
        self.catalog_service = CatalogApplicationService(catalog_repo)
        self.cadence_evaluator = CadencePolicyEvaluator(catalog_repo)
        self._checkpoint_lock = threading.Lock()

    def load_configs(self) -> List[SourceConfig]:
        """Carga y valida la lista de fuentes desde el archivo YAML centralizado."""
        if not self.config_path or not self.config_path.exists():
            raise FileNotFoundError(f"Archivo de configuración de fuentes no encontrado: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        sources_data = data.get("sources", [])
        return [SourceConfig.model_validate(s) for s in sources_data]

    def run_batch(
        self,
        source_id_filter: Optional[str] = None,
        force: bool = False
    ) -> RunReport:
        """
        Ejecuta una corrida batch sobre las fuentes configuradas y elegibles.
        Realiza checkpoint incremental por cada fuente terminada.
        """
        started_at = datetime.now(timezone.utc)
        run_id = f"run_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        logger.info(f"=== Iniciando Corrida Batch [{run_id}] ===")

        all_configs = self.load_configs()

        # Filtrar por fuente específica si fue solicitada por el usuario
        if source_id_filter:
            target_configs = [c for c in all_configs if c.source_id.lower() == source_id_filter.lower()]
            if not target_configs:
                raise ValueError(f"Fuente '{source_id_filter}' no encontrada en la configuración")
        else:
            target_configs = all_configs

        aggregator = ResultAggregator()

        eligible_configs: List[SourceConfig] = []
        for config in target_configs:
            source_state = self.catalog_repo.get_source(config.source_id)

            # 1. Evaluar elegibilidad operativa
            is_eligible, skip_reason = self.status_gate.evaluate_eligibility(
                config=config,
                source_state=source_state,
                force=force,
                now=started_at
            )

            if not is_eligible:
                logger.info(f"Omitiendo fuente [{config.source_id}]: {skip_reason}")
                skipped_obs = SourceRunObservation(
                    source_id=config.source_id,
                    run_id=run_id,
                    workflow=config.workflow,
                    execution_status=ExecutionStatus.SKIPPED,
                    content_status=ContentStatus.NOT_EVALUATED,
                    skip_reason=skip_reason,
                    observed_at=datetime.now(timezone.utc)
                )
                self.catalog_repo.save_source_observation(skipped_obs)
                aggregator.add_observation(skipped_obs)
            else:
                eligible_configs.append(config)

        # 2. Despachar fuentes elegibles concurrentemente mediante AsyncWorkerPool (Bulkhead)
        def _process_source(cfg: SourceConfig) -> SourceRunObservation:
            logger.info(f"Procesando fuente [{cfg.source_id}] con workflow [{cfg.workflow}]...")
            extraction_result = self.dispatcher.dispatch(cfg)

            # 3. Checkpoint incremental thread-safe inmediato
            with self._checkpoint_lock:
                obs = self.catalog_service.reconcile_and_checkpoint(
                    config=cfg,
                    run_id=run_id,
                    extraction_result=extraction_result
                )
                # 4. Actualizar estado operativo y cadencia posterior
                self.cadence_evaluator.evaluate_and_update(cfg, obs)
                # 5. Acumular observación
                aggregator.add_observation(obs)
                return obs

        futures = [
            self.worker_pool.submit_workflow(_process_source, cfg)
            for cfg in eligible_configs
        ]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                logger.critical(f"Error procesando fuente en pool: {e}", exc_info=True)

        # 6. Construir y persistir reporte estructurado consolidado
        finished_at = datetime.now(timezone.utc)
        report = RunReportBuilder.build(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            total_selected=len(target_configs),
            aggregator=aggregator
        )
        self.report_repo.save_report(report)

        logger.info(f"=== Corrida [{run_id}] Finalizada ===")
        logger.info(
            f"Procesadas: {report.sources_processed} | "
            f"Omitidas: {report.sources_skipped} | "
            f"Fallidas: {report.sources_failed} | "
            f"Con cambios: {report.sources_with_changes}"
        )

        return report
