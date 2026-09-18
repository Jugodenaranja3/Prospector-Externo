"""RunOrchestrator async con runtime HTTP compartido por corrida."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

import yaml

from prospector_externo.application.aggregator import ResultAggregator
from prospector_externo.application.cadence_evaluator import CadencePolicyEvaluator
from prospector_externo.application.catalog_service import CatalogApplicationService
from prospector_externo.application.dispatcher import SourceDispatcher
from prospector_externo.application.report_builder import RunReportBuilder
from prospector_externo.application.status_gate import SourceStatusGate
from prospector_externo.domain.models import SourceConfig
from prospector_externo.domain.observations import (
    ContentStatus,
    ExecutionStatus,
    RunReport,
    SourceRunObservation,
)
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort

logger = logging.getLogger("prospector.application.orchestrator")


class RunOrchestrator:
    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        report_repo: RunReportRepositoryPort,
        config_path: Optional[Path] = None,
        worker_pool=None,  # compatibilidad temporal con callers legacy
        http_runtime_factory: Optional[Callable[[], AsyncHttpRuntime]] = None,
        max_source_concurrency: int = 4,
    ) -> None:
        self.catalog_repo = catalog_repo
        self.report_repo = report_repo
        self.config_path = config_path
        self.status_gate = SourceStatusGate()
        self.dispatcher = SourceDispatcher()
        self.catalog_service = CatalogApplicationService(catalog_repo)
        self.cadence_evaluator = CadencePolicyEvaluator(catalog_repo)
        self.http_runtime_factory = http_runtime_factory or AsyncHttpRuntime
        self.max_source_concurrency = max(1, max_source_concurrency)

    def load_configs(self) -> List[SourceConfig]:
        if not self.config_path or not self.config_path.exists():
            raise FileNotFoundError(
                f"Archivo de configuración de fuentes no encontrado: {self.config_path}"
            )
        with open(self.config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return [SourceConfig.model_validate(item) for item in data.get("sources", [])]

    async def run_batch_async(
        self,
        source_id_filter: Optional[str] = None,
        force: bool = False,
    ) -> RunReport:
        started_at = datetime.now(timezone.utc)
        run_id = f"run_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        all_configs = self.load_configs()

        if source_id_filter:
            target_configs = [
                cfg for cfg in all_configs
                if cfg.source_id.lower() == source_id_filter.lower()
            ]
            if not target_configs:
                raise ValueError(
                    f"Fuente '{source_id_filter}' no encontrada en la configuración"
                )
        else:
            target_configs = all_configs

        aggregator = ResultAggregator()
        eligible: List[SourceConfig] = []

        for config in target_configs:
            source_state = self.catalog_repo.get_source(config.source_id)
            is_eligible, skip_reason = self.status_gate.evaluate_eligibility(
                config=config,
                source_state=source_state,
                force=force,
                now=started_at,
            )
            if is_eligible:
                eligible.append(config)
                continue

            obs = SourceRunObservation(
                source_id=config.source_id,
                run_id=run_id,
                workflow=config.workflow,
                execution_status=ExecutionStatus.SKIPPED,
                content_status=ContentStatus.NOT_EVALUATED,
                skip_reason=skip_reason,
                observed_at=datetime.now(timezone.utc),
            )
            self.catalog_repo.save_source_observation(obs)
            aggregator.add_observation(obs)

        runtime = self.http_runtime_factory()
        self.dispatcher.set_http_runtime(runtime)
        semaphore = asyncio.Semaphore(self.max_source_concurrency)

        async def process_source(config: SourceConfig) -> None:
            async with semaphore:
                dispatched = self.dispatcher.dispatch(config)
                extraction = await dispatched if inspect.isawaitable(dispatched) else dispatched
                obs = self.catalog_service.reconcile_and_checkpoint(
                    config=config,
                    run_id=run_id,
                    extraction_result=extraction,
                )
                self.cadence_evaluator.evaluate_and_update(config, obs)
                aggregator.add_observation(obs)

        try:
            await asyncio.gather(*(process_source(cfg) for cfg in eligible))
        finally:
            await runtime.aclose()

        finished_at = datetime.now(timezone.utc)
        report = RunReportBuilder.build(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            total_selected=len(target_configs),
            aggregator=aggregator,
        )
        self.report_repo.save_report(report)
        return report

    def run_batch(
        self,
        source_id_filter: Optional[str] = None,
        force: bool = False,
    ) -> RunReport:
        """Wrapper sync de compatibilidad. El CLI usa run_batch_async directamente."""
        return asyncio.run(
            self.run_batch_async(
                source_id_filter=source_id_filter,
                force=force,
            )
        )
