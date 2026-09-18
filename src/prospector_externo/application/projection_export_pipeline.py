"""Pipeline offline catálogo → proyección DATAX → JSON legacy.

Consume únicamente estado ya persistido por el Prospector. No realiza HTTP y no
modifica snapshots ni recursos brutos. Los artefactos downstream viven separados
bajo ``<output>/<source_id>/downstream``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from prospector_externo.application.legacy_stats_exporter import (
    LegacyExportPlan,
    LegacyStatsJsonExporter,
)
from prospector_externo.application.projection_service import DataxProjectionService
from prospector_externo.domain.projection import DataxProjection
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort


class ProjectionExportManifest(BaseModel):
    """Resumen trazable y reproducible de una exportación downstream."""

    schema_version: str = "datax-export-manifest-1.0"
    source_id: str
    run_id: str
    input_resources_hash: str
    raw_resources: int
    selected_resources: int
    families: int
    legacy_records: int
    projection_sha256: str
    legacy_sha256: str
    projection_relpath: str
    legacy_relpath: str


class DataxProjectionExportPipeline:
    """Orquesta proyección y compatibilidad legacy sobre un snapshot existente."""

    def __init__(self, catalog_repo: CatalogRepositoryPort):
        self.catalog_repo = catalog_repo
        self.projection_service = DataxProjectionService()

    @staticmethod
    def _atomic_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            temp_name = None
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def build_projection(self, source_id: str) -> DataxProjection:
        source = self.catalog_repo.get_source(source_id)
        if source is None:
            raise ValueError(f"Fuente no encontrada en catálogo: {source_id!r}")
        snapshot = self.catalog_repo.get_latest_snapshot(source_id)
        if snapshot is None:
            raise ValueError(f"No existe snapshot para la fuente: {source_id!r}")
        return self.projection_service.project(source, snapshot)

    def export(
        self,
        source_id: str,
        base_output_dir: str | Path,
        *,
        legacy_plan: Optional[LegacyExportPlan] = None,
    ) -> ProjectionExportManifest:
        projection = self.build_projection(source_id)
        base = Path(base_output_dir)
        downstream_dir = base / source_id / "downstream"
        projection_path = downstream_dir / "datax_projection.json"
        legacy_path = downstream_dir / "legacy_estadisticas.json"
        manifest_path = downstream_dir / "export_manifest.json"

        self._atomic_json(projection_path, projection.model_dump(mode="json"))
        legacy_result = LegacyStatsJsonExporter.write_atomic(
            legacy_path,
            projection,
            legacy_plan,
        )

        projection_relpath = projection_path.relative_to(base).as_posix()
        legacy_relpath = legacy_path.relative_to(base).as_posix()
        manifest = ProjectionExportManifest(
            source_id=projection.source_id,
            run_id=projection.run_id,
            input_resources_hash=projection.resources_hash,
            raw_resources=projection.total_raw_resources,
            selected_resources=projection.total_selected_resources,
            families=projection.total_families,
            legacy_records=legacy_result.record_count,
            projection_sha256=self._sha256(projection_path),
            legacy_sha256=self._sha256(legacy_path),
            projection_relpath=projection_relpath,
            legacy_relpath=legacy_relpath,
        )
        self._atomic_json(manifest_path, manifest.model_dump(mode="json"))
        return manifest
