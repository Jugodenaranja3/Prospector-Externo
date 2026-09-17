"""
Adaptador de persistencia MongoDB para el Prospector Externo.
Implementa el modelo de 7 colecciones definido en la Sección 16 del documento de arquitectura.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort
from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate
from prospector_externo.domain.observations import SourceRunObservation, RunReport

logger = logging.getLogger("prospector.adapters.mongo")

try:
    import pymongo
    from pymongo import MongoClient
    HAS_PYMONGO = True
except ImportError:
    HAS_PYMONGO = False


class MongoPersistenceAdapter(CatalogRepositoryPort, RunReportRepositoryPort):
    """Adaptador de persistencia sobre MongoDB."""

    def __init__(self, connection_uri: str = "mongodb://localhost:27017", database_name: str = "prospector_externo"):
        if not HAS_PYMONGO:
            raise RuntimeError("pymongo no está instalado. Ejecuta: pip install pymongo")

        self.client = MongoClient(connection_uri)
        self.db = self.client[database_name]

        # Colecciones según Sección 16 del documento de arquitectura
        self.sources_col = self.db["sources"]
        self.runs_col = self.db["runs"]
        self.discovered_urls_col = self.db["discovered_urls"]
        self.resource_candidates_col = self.db["resource_candidates"]
        self.snapshots_col = self.db["snapshots"]
        self.source_observations_col = self.db["source_observations"]
        self.run_reports_col = self.db["run_reports"]

        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        """Crea los índices recomendados para garantizar idempotencia y rapidez."""
        try:
            self.sources_col.create_index("source_id", unique=True)
            self.snapshots_col.create_index([("source_id", 1), ("run_id", 1)], unique=True)
            self.source_observations_col.create_index([("source_id", 1), ("run_id", 1)], unique=True)
            self.run_reports_col.create_index("run_id", unique=True)
            self.resource_candidates_col.create_index([("resource_key", 1), ("source_id", 1)])
        except Exception as e:
            logger.warning(f"Error asegurando índices de MongoDB: {e}")

    def save_snapshot(self, snapshot: Snapshot) -> None:
        doc = snapshot.model_dump(mode="json")
        self.snapshots_col.update_one(
            {"source_id": snapshot.source_id, "run_id": snapshot.run_id},
            {"$set": doc},
            upsert=True
        )
        # Guardar recursos en resource_candidates
        for r in snapshot.resources:
            r_doc = r.model_dump(mode="json")
            self.resource_candidates_col.update_one(
                {"resource_key": r.resource_key, "source_id": r.source_id},
                {"$set": r_doc},
                upsert=True
            )

    def get_latest_snapshot(self, source_id: str) -> Optional[Snapshot]:
        doc = self.snapshots_col.find_one({"source_id": source_id}, sort=[("captured_at", -1)])
        if doc:
            doc.pop("_id", None)
            return Snapshot.model_validate(doc)
        return None

    def save_source_observation(self, observation: SourceRunObservation) -> None:
        doc = observation.model_dump(mode="json")
        self.source_observations_col.update_one(
            {"source_id": observation.source_id, "run_id": observation.run_id},
            {"$set": doc},
            upsert=True
        )

    def get_source_observations(self, source_id: str, limit: int = 20) -> List[SourceRunObservation]:
        cursor = self.source_observations_col.find({"source_id": source_id}).sort("observed_at", -1).limit(limit)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(SourceRunObservation.model_validate(doc))
        return results

    def save_source(self, source: Source) -> None:
        doc = source.model_dump(mode="json")
        self.sources_col.update_one(
            {"source_id": source.source_id},
            {"$set": doc},
            upsert=True
        )

    def get_source(self, source_id: str) -> Optional[Source]:
        doc = self.sources_col.find_one({"source_id": source_id})
        if doc:
            doc.pop("_id", None)
            return Source.model_validate(doc)
        return None

    def list_sources(self) -> List[Source]:
        cursor = self.sources_col.find()
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(Source.model_validate(doc))
        return results

    def list_resources(self, source_id: Optional[str] = None) -> List[ResourceCandidate]:
        query = {"source_id": source_id} if source_id else {}
        cursor = self.resource_candidates_col.find(query)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(ResourceCandidate.model_validate(doc))
        return results

    def save_report(self, report: RunReport) -> None:
        doc = report.model_dump(mode="json")
        self.run_reports_col.update_one(
            {"run_id": report.run_id},
            {"$set": doc},
            upsert=True
        )

    def get_report(self, run_id: str) -> Optional[RunReport]:
        doc = self.run_reports_col.find_one({"run_id": run_id})
        if doc:
            doc.pop("_id", None)
            return RunReport.model_validate(doc)
        return None

    def list_reports(self, limit: int = 50) -> List[RunReport]:
        cursor = self.run_reports_col.find().sort("finished_at", -1).limit(limit)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(RunReport.model_validate(doc))
        return results
