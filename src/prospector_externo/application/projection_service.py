"""Servicio de aplicación para construir la proyección DATAX desde un snapshot."""

from prospector_externo.domain.models import Source, Snapshot
from prospector_externo.domain.projection import DataxProjection, ProjectionBuilder


class DataxProjectionService:
    """Pure application service: no HTTP, no persistence, no mutation del catálogo."""

    def project(self, source: Source, snapshot: Snapshot) -> DataxProjection:
        if source.source_id != snapshot.source_id:
            raise ValueError(
                f"Source/Snapshot incompatibles: {source.source_id!r} != {snapshot.source_id!r}"
            )
        return ProjectionBuilder.build(
            source_id=source.source_id,
            source_name=source.name,
            entrypoint=source.entrypoint,
            run_id=snapshot.run_id,
            resources_hash=snapshot.resources_hash,
            resources=snapshot.resources,
        )
