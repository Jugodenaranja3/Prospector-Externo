"""
Modelos de dominio principales del Prospector Externo.
Entidades puras desacopladas de librerías externas y de persistencia.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import uuid


class ChangeStatus(str, Enum):
    NEW = "NEW"
    UNCHANGED = "UNCHANGED"
    MODIFIED = "MODIFIED"
    REMOVED = "REMOVED"
    NOT_EVALUATED = "NOT_EVALUATED"


class DiscoveryType(str, Enum):
    HTML = "html"
    JAVASCRIPT = "javascript"
    COMMENTED_HTML = "commented_html"
    API = "api"
    ARCHIVE_INTERNAL = "archive_internal"
    CUSTOM = "custom"


class SourceConfig(BaseModel):
    """Configuración declarativa de una fuente cargada desde sources.yaml."""
    source_id: str
    name: str = ""
    entrypoint: str
    workflow: str = "html"
    seeds: List[str] = Field(default_factory=list)
    update_category: str = "DAILY"
    allowed_extensions: List[str] = Field(default_factory=lambda: [".pdf", ".xlsx", ".csv", ".zip"])
    excluded_path_keywords: List[str] = Field(default_factory=list)
    ignore_robots_txt: bool = False
    robots_override_reason: Optional[str] = None
    rate_limit_seconds: float = 1.0


class DiscoveredUrl(BaseModel):
    """Registro de una página o URL descubierta durante el recorrido."""
    normalized_url: str
    raw_url: str
    source_id: str
    discovery_type: DiscoveryType = DiscoveryType.HTML
    parent_url: Optional[str] = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    http_status: Optional[int] = None


class ResourceCandidate(BaseModel):
    """Recurso o documento público identificado (PDF, Excel, ZIP, etc.)."""
    resource_key: str
    url: str
    source_id: str
    title: str = ""
    file_extension: str = ""
    content_type: Optional[str] = None
    content_length_bytes: Optional[int] = None
    last_modified_header: Optional[str] = None
    etag: Optional[str] = None
    discovered_from_url: Optional[str] = None
    extracted_from_archive: Optional[str] = None
    period_label: Optional[str] = None
    content_hash: Optional[str] = None
    change_status: ChangeStatus = ChangeStatus.NEW
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Snapshot(BaseModel):
    """Foto instantánea del inventario de una fuente en una corrida."""
    snapshot_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    run_id: str
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resources_hash: str
    total_resources: int
    resources: List[ResourceCandidate] = Field(default_factory=list)


class Source(BaseModel):
    """Entidad representativa de una fuente en el catálogo maestro."""
    source_id: str
    name: str
    entrypoint: str
    workflow: str
    health_status: str = "ACTIVE"
    update_category: str = "DAILY"
    next_eligible_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_run_id: Optional[str] = None
    last_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
