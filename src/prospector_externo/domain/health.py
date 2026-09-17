"""
Máquina de estados de salud para fuentes estatales del Prospector Externo.
Distingue salud operativa (ACTIVE, WARNING, SUSPENDED) de resultados de contenido.
"""

from enum import Enum
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class HealthStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WARNING = "WARNING"
    SUSPENDED = "SUSPENDED"


class HealthStateMachine(BaseModel):
    """Administra las transiciones de salud de una fuente."""
    current_status: HealthStatus = HealthStatus.ACTIVE
    consecutive_failures: int = 0
    failure_threshold: int = 3
    last_transition_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_error_reason: Optional[str] = None

    def record_success(self) -> None:
        """Registra una corrida exitosa y restablece el estado a ACTIVE."""
        self.consecutive_failures = 0
        self.last_error_reason = None
        if self.current_status != HealthStatus.ACTIVE:
            self.current_status = HealthStatus.ACTIVE
            self.last_transition_at = datetime.now(timezone.utc)

    def record_failure(self, reason: str) -> HealthStatus:
        """Registra un fallo. Suspende si se alcanza el umbral de fallos consecutivos."""
        self.consecutive_failures += 1
        self.last_error_reason = reason
        self.last_transition_at = datetime.now(timezone.utc)

        if self.consecutive_failures >= self.failure_threshold:
            self.current_status = HealthStatus.SUSPENDED
        else:
            self.current_status = HealthStatus.WARNING

        return self.current_status

    def can_crawl(self) -> bool:
        """Indica si la fuente tiene un estado que permite exploración."""
        return self.current_status != HealthStatus.SUSPENDED
