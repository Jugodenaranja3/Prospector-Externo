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

    @staticmethod
    def _resource_evidence(resource: ResourceCandidate) -> str:
        """Evidencia estable de contenido sin depender de metadata volátil de discovery."""
        if resource.content_hash:
            return f"sha256:{resource.content_hash}"
        if resource.etag:
            return f"etag:{resource.etag}"
        if resource.last_modified_header:
            return f"last-modified:{resource.last_modified_header}"
        return ""

    def compute_resources_hash(self, resources: List[ResourceCandidate]) -> str:
        """Hash consolidado por identidad de recurso + evidencia de contenido disponible."""
        signatures = []
        for resource in resources:
            evidence = self._resource_evidence(resource)
            signatures.append(
                f"{resource.resource_key}|{evidence}" if evidence else resource.resource_key
            )
        combined = "|".join(sorted(signatures))
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
        historical_hash = None
        if latest_snapshot:
            historical_keys = {r.resource_key: r for r in latest_snapshot.resources}
            # Recalcular desde los recursos permite migrar snapshots antiguos cuyo
            # resources_hash solo consideraba resource_key.
            historical_hash = self.compute_resources_hash(latest_snapshot.resources)

        # Clasificar cambios por identidad y, cuando existe, evidencia de contenido.
        for r in extraction_result.resources:
            historical = historical_keys.get(r.resource_key)
            if historical is None:
                r.change_status = ChangeStatus.NEW
                continue
            previous_evidence = self._resource_evidence(historical)
            current_evidence = self._resource_evidence(r)
            if previous_evidence != current_evidence and (previous_evidence or current_evidence):
                r.change_status = ChangeStatus.MODIFIED
            else:
                r.change_status = ChangeStatus.UNCHANGED

        # Evaluar estado de contenido
        if len(extraction_result.resources) == 0:
            content_status = ContentStatus.EMPTY_RESULT
        elif latest_snapshot is None:
            content_status = ContentStatus.CHANGED
        elif historical_hash != current_hash:
            content_status = ContentStatus.CHANGED
        else:
            content_status = ContentStatus.NO_CHANGE

        # Actualizar primero la entidad Source para que adaptadores de salida
        # puedan exportar metadata real en el mismo checkpoint inicial.
        source_entity = self.catalog_repo.get_source(source_id) or Source(
            source_id=source_id,
            name=config.name or source_id.upper(),
            entrypoint=config.entrypoint,
            workflow=config.workflow,
            update_category=config.update_category,
        )
        source_entity.name = config.name or source_entity.name or source_id.upper()
        source_entity.entrypoint = config.entrypoint
        source_entity.workflow = config.workflow
        source_entity.last_run_id = run_id
        source_entity.last_run_at = now
        source_entity.updated_at = now
        self.catalog_repo.save_source(source_entity)

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
