"""Observaciones y reportes de corrida trazables."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
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
    """Métricas técnicas de cobertura de una fuente durante una corrida."""

    pages_visited: int = 0
    urls_discovered: int = 0
    resources_found: int = 0
    urls_rejected: int = 0
    urls_failed: int = 0
    urls_pending: int = 0
    requests_total: int = 0
    http_403: int = 0
    http_429: int = 0
    timeouts: int = 0
    robots_disallowed: int = 0
    sitemap_documents: int = 0
    sitemap_urls: int = 0
    sitemap_errors: int = 0
    spider_traps_blocked: int = 0
    query_variants_blocked: int = 0
    pagination_pages: int = 0
    pagination_families_stopped: int = 0
    api_endpoints: int = 0
    api_documentation_found: int = 0
    openapi_documents: int = 0
    api_non_get_operations_skipped: int = 0
    api_auth_required: int = 0
    api_unresolved_operations: int = 0
    api_pages_visited: int = 0
    api_records_sampled: int = 0
    api_pagination_stopped: int = 0
    api_documents_probed: int = 0
    api_document_errors: int = 0
    stop_reason: Optional[str] = None


class SourceRunObservation(BaseModel):
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
