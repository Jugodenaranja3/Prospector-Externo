"""
Modelos de observaciones y reportes de corrida para el Prospector Externo.
Soporta trazabilidad estricta y consolidación de métricas de cobertura.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import uuid


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"


class ContentStatus(str, Enum):
    CHANGED = "CHANGED"
    NO_CHANGE = "NO_CHANGE"
    EMPTY_RESULT = "EMPTY_RESULT"
    NOT_EVALUATED = "NOT_EVALUATED"


class CoverageStats(BaseModel):
    """Métricas de cobertura y exhaustividad de la exploración."""
    pages_visited: int = 0
    urls_discovered: int = 0
    resources_found: int = 0
    urls_rejected: int = 0
    urls_failed: int = 0


class SourceRunObservation(BaseModel):
    """Resultado operativo y de contenido de una fuente en una corrida."""
    observation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    run_id: str
    workflow: str
    execution_status: ExecutionStatus
    content_status: ContentStatus
    failure_code: Optional[str] = None
    skip_reason: Optional[str] = None
    robots_override_applied: bool = False
    robots_override_reason: Optional[str] = None
    coverage: CoverageStats = Field(default_factory=CoverageStats)
    next_eligible_at: Optional[datetime] = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunReport(BaseModel):
    """Reporte estructurado consolidado al finalizar una corrida oportuna."""
    run_id: str
    started_at: datetime
    finished_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sources_selected: int = 0
    sources_processed: int = 0
    sources_skipped: int = 0
    sources_failed: int = 0
    sources_with_changes: int = 0
    sources_without_changes: int = 0
    source_results: List[SourceRunObservation] = Field(default_factory=list)
