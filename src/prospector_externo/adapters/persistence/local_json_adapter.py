"""
Adaptador de persistencia en archivos JSON locales.
Mantiene compatibilidad con contratos de salida (Standard, Tree, Compact)
y almacena reportes de corrida y estado histórico en disco.
"""

import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort
from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate
from prospector_externo.domain.observations import SourceRunObservation, RunReport


class LocalJsonRepositoryAdapter(CatalogRepositoryPort, RunReportRepositoryPort):
    """Implementa persistencia local en archivos JSON con particionamiento por fuente."""

    def __init__(self, base_output_dir: Path):
        self.base_dir = Path(base_output_dir)
        self.state_dir = self.base_dir / "state"
        self.reports_dir = self.base_dir / "reports"

        # Crear estructura de carpetas
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "snapshots").mkdir(exist_ok=True)
        (self.state_dir / "observations").mkdir(exist_ok=True)

    def save_snapshot(self, snapshot: Snapshot) -> None:
        """Guarda snapshot en carpeta de estado y exporta los 3 formatos JSON en output/<source_id>/."""
        # 1. Guardar copia histórica de snapshot
        snap_path = self.state_dir / "snapshots" / f"{snapshot.source_id}_{snapshot.run_id}.json"
        with open(snap_path, "w", encoding="utf-8") as f:
            f.write(snapshot.model_dump_json(indent=2))

        # 2. Exportar en la carpeta dedicada de la fuente
        source_dir = self.base_dir / snapshot.source_id
        source_dir.mkdir(parents=True, exist_ok=True)

        # 2.1 Contrato Estándar
        source_state = self.get_source(snapshot.source_id)
        source_name = source_state.name if source_state else snapshot.source_id.upper()
        source_entrypoint = source_state.entrypoint if source_state else ""
        standard_contract = {
            "version": "1.0.0",
            "source": {
                "id": snapshot.source_id,
                "name": source_name,
                "entrypoint": source_entrypoint
            },
            "run": {
                "run_id": snapshot.run_id,
                "timestamp": snapshot.captured_at.isoformat(),
                "status": "SUCCESS"
            },
            "datasets": [
                {
                    "dataset_id": f"{snapshot.source_id}_dataset",
                    "title": f"Recursos Públicos — {snapshot.source_id.upper()}",
                    "resources": [r.model_dump() for r in snapshot.resources]
                }
            ],
            "metadata": {
                "generated_by": "prospector-externo",
                "total_resources": snapshot.total_resources,
                "resources_hash": snapshot.resources_hash
            }
        }
        with open(source_dir / f"mapa_{snapshot.source_id}.json", "w", encoding="utf-8") as f:
            json.dump(standard_contract, f, indent=2, ensure_ascii=False, default=str)

        # 2.2 Formato Compacto IA
        compact = {
            "source_id": snapshot.source_id,
            "run_id": snapshot.run_id,
            "total": snapshot.total_resources,
            "resources": [
                {
                    "key": r.resource_key,
                    "url": r.url,
                    "title": r.title,
                    "ext": r.file_extension,
                    "status": r.change_status,
                    "extracted_from": r.extracted_from_archive,
                    "resource_type": r.resource_type,
                    "api": r.api.model_dump(mode="json") if r.api is not None else None,
                }
                for r in snapshot.resources
            ]
        }
        with open(source_dir / f"mapa_{snapshot.source_id}_compact.json", "w", encoding="utf-8") as f:
            json.dump(compact, f, indent=2, ensure_ascii=False, default=str)

        # 2.3 Formato Árbol Jerárquico
        tree = {
            "name": f"Raíz {snapshot.source_id.upper()}",
            "type": "source",
            "children": [
                {
                    "name": r.title or r.resource_key,
                    "type": "resource",
                    "url": r.url,
                    "extension": r.file_extension,
                    "status": r.change_status,
                    "resource_type": r.resource_type,
                    "api": r.api.model_dump(mode="json") if r.api is not None else None,
                }
                for r in snapshot.resources
            ]
        }
        with open(source_dir / f"mapa_{snapshot.source_id}_tree.json", "w", encoding="utf-8") as f:
            json.dump(tree, f, indent=2, ensure_ascii=False, default=str)

    def get_latest_snapshot(self, source_id: str) -> Optional[Snapshot]:
        """Busca el snapshot más reciente para una fuente."""
        snapshots_dir = self.state_dir / "snapshots"
        matching = sorted(snapshots_dir.glob(f"{source_id}_*.json"), reverse=True)
        if not matching:
            return None

        try:
            with open(matching[0], "r", encoding="utf-8") as f:
                data = json.load(f)
                return Snapshot.model_validate(data)
        except Exception:
            return None

    def save_source_observation(self, observation: SourceRunObservation) -> None:
        """Guarda observación de la corrida para la fuente."""
        obs_file = self.state_dir / "observations" / f"{observation.source_id}_{observation.run_id}.json"
        with open(obs_file, "w", encoding="utf-8") as f:
            f.write(observation.model_dump_json(indent=2))

    def get_source_observations(self, source_id: str, limit: int = 20) -> List[SourceRunObservation]:
        """Recupera observaciones históricas de una fuente."""
        obs_dir = self.state_dir / "observations"
        files = sorted(obs_dir.glob(f"{source_id}_*.json"), reverse=True)[:limit]
        results = []
        for file in files:
            try:
                with open(file, "r", encoding="utf-8") as f:
                    results.append(SourceRunObservation.model_validate(json.load(f)))
            except Exception:
                pass
        return results

    def save_source(self, source: Source) -> None:
        """Persiste o actualiza la fuente en el archivo maestro sources.json."""
        sources_file = self.state_dir / "sources.json"
        sources_dict = {}
        if sources_file.exists():
            try:
                with open(sources_file, "r", encoding="utf-8") as f:
                    sources_dict = json.load(f)
            except Exception:
                pass

        sources_dict[source.source_id] = source.model_dump(mode="json")
        with open(sources_file, "w", encoding="utf-8") as f:
            json.dump(sources_dict, f, indent=2, ensure_ascii=False)

    def get_source(self, source_id: str) -> Optional[Source]:
        """Obtiene una fuente registrada."""
        sources_file = self.state_dir / "sources.json"
        if not sources_file.exists():
            return None
        try:
            with open(sources_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if source_id in data:
                    return Source.model_validate(data[source_id])
        except Exception:
            pass
        return None

    def list_sources(self) -> List[Source]:
        """Lista todas las fuentes registradas."""
        sources_file = self.state_dir / "sources.json"
        if not sources_file.exists():
            return []
        try:
            with open(sources_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [Source.model_validate(item) for item in data.values()]
        except Exception:
            return []

    def list_resources(self, source_id: Optional[str] = None) -> List[ResourceCandidate]:
        """Lista los recursos del snapshot más reciente."""
        if source_id:
            snap = self.get_latest_snapshot(source_id)
            return snap.resources if snap else []

        # Todos los recursos de todas las fuentes
        all_resources = []
        for src in self.list_sources():
            snap = self.get_latest_snapshot(src.source_id)
            if snap:
                all_resources.extend(snap.resources)
        return all_resources

    def save_report(self, report: RunReport) -> None:
        """Persiste el RunReport de una corrida."""
        report_file = self.reports_dir / f"run_{report.run_id}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))

    def get_report(self, run_id: str) -> Optional[RunReport]:
        """Recupera un reporte de corrida por su ID."""
        report_file = self.reports_dir / f"run_{run_id}.json"
        if not report_file.exists():
            return None
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                return RunReport.model_validate(json.load(f))
        except Exception:
            return None

    def list_reports(self, limit: int = 50) -> List[RunReport]:
        """Lista los reportes de corrida más recientes."""
        reports = []
        files = sorted(self.reports_dir.glob("run_*.json"), reverse=True)[:limit]
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fl:
                    reports.append(RunReport.model_validate(json.load(fl)))
            except Exception:
                pass
        return reports
