"""
Evaluador de política de cadencia y actualización de salud (CadencePolicyEvaluator).
Ajusta conservadoramente la frecuencia de ejecución únicamente ante observaciones exitosas sin novedades.
"""

from datetime import datetime, timezone, timedelta
import logging

from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.domain.models import Source, SourceConfig
from prospector_externo.domain.observations import SourceRunObservation, ExecutionStatus, ContentStatus
from prospector_externo.domain.health import HealthStateMachine, HealthStatus
from prospector_externo.domain.cadence import CadenceState, UpdateCategory

logger = logging.getLogger("prospector.application.cadence")


class CadencePolicyEvaluator:
    """Actualiza salud y próxima fecha de elegibilidad según el resultado de la corrida."""

    def __init__(self, catalog_repo: CatalogRepositoryPort):
        self.catalog_repo = catalog_repo

    def evaluate_and_update(
        self,
        config: SourceConfig,
        observation: SourceRunObservation
    ) -> None:
        source_id = config.source_id
        source = self.catalog_repo.get_source(source_id)
        if not source:
            return

        now = datetime.now(timezone.utc)

        # 1. Si la ejecución falló
        if observation.execution_status == ExecutionStatus.FAILED:
            health = HealthStateMachine(current_status=HealthStatus(source.health_status))
            new_health = health.record_failure(reason=observation.failure_code or "ERROR")
            source.health_status = new_health.value
            logger.warning(f"Salud de [{source_id}] actualizada a: {source.health_status} por error: {observation.failure_code}")

            # Reintento corto (ej. 4 horas) para errores no permanentes si no está suspendida
            if new_health != HealthStatus.SUSPENDED:
                source.next_eligible_at = now + timedelta(hours=4)
            self.catalog_repo.save_source(source)
            return

        # 2. Si la ejecución fue exitosa
        health = HealthStateMachine(current_status=HealthStatus(source.health_status))
        health.record_success()
        source.health_status = health.current_status.value

        cadence = CadenceState(
            category=UpdateCategory(source.update_category),
            next_eligible_at=source.next_eligible_at
        )

        if observation.content_status == ContentStatus.CHANGED:
            # Restablecer cadencia base configurada
            base_cat = UpdateCategory(config.update_category)
            cadence.register_change(base_cat)
            source.update_category = cadence.category.value
            source.next_eligible_at = cadence.next_eligible_at
            logger.info(f"Novedades detectadas en [{source_id}]. Cadencia restablecida a {source.update_category}")

        elif observation.content_status in (ContentStatus.NO_CHANGE, ContentStatus.EMPTY_RESULT):
            cadence.register_no_change()
            source.update_category = cadence.category.value
            source.next_eligible_at = cadence.next_eligible_at
            logger.info(f"Sin novedades en [{source_id}]. Próxima elegibilidad calculada: {source.next_eligible_at.isoformat()}")

        source.updated_at = now
        self.catalog_repo.save_source(source)
