"""
Controladores HTTP FastAPI para la fachada de consulta Catalog API Facade.
Solo lectura para consumidores externos (DATAX y Reportes Perdidos).
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort
from prospector_externo.domain.models import Source, ResourceCandidate
from prospector_externo.domain.observations import RunReport, SourceRunObservation


def create_catalog_router(
    catalog_repo: CatalogRepositoryPort,
    report_repo: RunReportRepositoryPort
) -> APIRouter:
    """Crea el enrutador de FastAPI inyectando los puertos de persistencia."""
    router = APIRouter()

    @router.get("/catalog/resources", response_model=List[ResourceCandidate])
    def get_resources(source_id: Optional[str] = Query(None, description="Filtrar por ID de fuente")):
        """Consulta recursos descubiertos con filtros básicos."""
        return catalog_repo.list_resources(source_id=source_id)

    @router.get("/catalog/sources", response_model=List[Source])
    def get_sources():
        """Consulta las fuentes registradas y su estado operativo."""
        return catalog_repo.list_sources()

    @router.get("/catalog/sources/{source_id}", response_model=Source)
    def get_source_detail(source_id: str):
        """Consulta el detalle y estado operativo de una fuente."""
        src = catalog_repo.get_source(source_id)
        if not src:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        return src

    @router.get("/runs", response_model=List[RunReport])
    def list_runs(limit: int = Query(20, ge=1, le=100)):
        """Lista las corridas recientes."""
        return report_repo.list_reports(limit=limit)

    @router.get("/runs/{run_id}/report", response_model=RunReport)
    def get_run_report(run_id: str):
        """Obtiene el reporte consolidado de una corrida."""
        rep = report_repo.get_report(run_id)
        if not rep:
            raise HTTPException(status_code=404, detail=f"Reporte de corrida '{run_id}' no encontrado")
        return rep

    @router.get("/sources/{source_id}/observations", response_model=List[SourceRunObservation])
    def get_observations(source_id: str, limit: int = Query(20, ge=1, le=50)):
        """Consulta el historial de observaciones de una fuente."""
        return catalog_repo.get_source_observations(source_id, limit=limit)

    return router
