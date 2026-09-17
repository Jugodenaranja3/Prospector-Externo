"""
Servicio de aplicación de catálogo (CatalogApplicationService).
Gestiona comparación de snapshots, checkpoints incrementales y creación de observaciones de corrida.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Tuple, List, Dict

from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.domain.models import Source, SourceConfig, Snapshot, ResourceCandidate, ChangeStatus
from prospector_externo.domain.observations import SourceRunObservation, ExecutionStatus, ContentStatus
from prospector_externo.kernel.contracts import ExtractionResult

logger = logging.getLogger("prospector.application.catalog")


class CatalogApplicationService:
    """Centraliza la reconciliación histórica, persistencia de snapshots y observaciones operativas."""

    def __init__(self, catalog_repo: CatalogRepositoryPort):
        self.catalog_repo = catalog_repo

    def compute_resources_hash(self, resources: List[ResourceCandidate]) -> str:
        """Calcula hash SHA-256 consolidado y determinista sobre las claves de recursos ordenadas."""
        sorted_keys = sorted(r.resource_key for r in resources)
        combined = "|".join(sorted_keys)
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def reconcile_and_checkpoint(
        self,
        config: SourceConfig,
        run_id: str,
        extraction_result: ExtractionResult
    ) -> SourceRunObservation:
        """
        Compara el resultado de la extracción contra el último snapshot,
        persiste el checkpoint de la fuente y genera la observación trazable.
        """
        source_id = config.source_id
        now = datetime.now(timezone.utc)

        # Si la extracción falló técnicamente
        if not extraction_result.success:
            obs = SourceRunObservation(
                source_id=source_id,
                run_id=run_id,
                workflow=config.workflow,
                execution_status=ExecutionStatus.FAILED,
                content_status=ContentStatus.NOT_EVALUATED,
                failure_code=extraction_result.failure_code,
                coverage=extraction_result.coverage,
                observed_at=now
            )
            self.catalog_repo.save_source_observation(obs)
            return obs

        # Extracción exitosa: Reconciliación con histórico
        latest_snapshot = self.catalog_repo.get_latest_snapshot(source_id)
        current_hash = self.compute_resources_hash(extraction_result.resources)

        historical_keys: Dict[str, ResourceCandidate] = {}
        if latest_snapshot:
            historical_keys = {r.resource_key: r for r in latest_snapshot.resources}

        # Clasificar cambios en recursos
        for r in extraction_result.resources:
            if r.resource_key not in historical_keys:
                r.change_status = ChangeStatus.NEW
            else:
                r.change_status = ChangeStatus.UNCHANGED

        # Evaluar estado de contenido
        if len(extraction_result.resources) == 0:
            content_status = ContentStatus.EMPTY_RESULT
        elif latest_snapshot is None:
            content_status = ContentStatus.CHANGED
        elif latest_snapshot.resources_hash != current_hash:
            content_status = ContentStatus.CHANGED
        else:
            content_status = ContentStatus.NO_CHANGE

        # Crear y persistir nuevo snapshot (Checkpoint incremental)
        new_snapshot = Snapshot(
            source_id=source_id,
            run_id=run_id,
            captured_at=now,
            resources_hash=current_hash,
            total_resources=len(extraction_result.resources),
            resources=extraction_result.resources
        )
        self.catalog_repo.save_snapshot(new_snapshot)
        logger.info(f"Checkpoint incremental persistido con éxito para [{source_id}] ({len(new_snapshot.resources)} recursos).")

        # Actualizar entidad Source en el catálogo
        source_entity = self.catalog_repo.get_source(source_id) or Source(
            source_id=source_id,
            name=config.name or source_id.upper(),
            entrypoint=config.entrypoint,
            workflow=config.workflow
        )
        source_entity.last_run_id = run_id
        source_entity.last_run_at = now
        source_entity.updated_at = now
        self.catalog_repo.save_source(source_entity)

        # Generar observación de corrida
        observation = SourceRunObservation(
            source_id=source_id,
            run_id=run_id,
            workflow=config.workflow,
            execution_status=ExecutionStatus.SUCCESS,
            content_status=content_status,
            robots_override_applied=config.ignore_robots_txt,
            robots_override_reason=config.robots_override_reason if config.ignore_robots_txt else None,
            coverage=extraction_result.coverage,
            observed_at=now
        )
        self.catalog_repo.save_source_observation(observation)
        return observation
