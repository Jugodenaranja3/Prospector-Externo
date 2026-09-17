"""
Contratos y DTOs de salida para los plugins de workflow del Kernel.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from prospector_externo.domain.models import ResourceCandidate, DiscoveredUrl
from prospector_externo.domain.observations import CoverageStats


class ExtractionResult(BaseModel):
    """Resultado devuelto por la ejecución de cualquier SourceWorkflow."""
    source_id: str
    success: bool = True
    resources: List[ResourceCandidate] = Field(default_factory=list)
    discovered_urls: List[DiscoveredUrl] = Field(default_factory=list)
    coverage: CoverageStats = Field(default_factory=CoverageStats)
    failure_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
