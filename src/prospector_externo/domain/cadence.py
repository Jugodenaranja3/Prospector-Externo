"""
Modelo de cadencia y planificación de ejecución para fuentes del Prospector Externo.
"""

from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, Field


class UpdateCategory(str, Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class CadenceState(BaseModel):
    """Estado de cadencia de actualización y elegibilidad temporal."""
    category: UpdateCategory = UpdateCategory.DAILY
    next_eligible_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    consecutive_no_change_count: int = 0
    last_evaluated_at: Optional[datetime] = None

    def calculate_next_run(self, now: Optional[datetime] = None) -> datetime:
        """Calcula la próxima fecha de elegibilidad según la categoría actual."""
        base_time = now or datetime.now(timezone.utc)
        if self.category == UpdateCategory.DAILY:
            self.next_eligible_at = base_time + timedelta(days=1)
        elif self.category == UpdateCategory.WEEKLY:
            self.next_eligible_at = base_time + timedelta(days=7)
        elif self.category == UpdateCategory.MONTHLY:
            self.next_eligible_at = base_time + timedelta(days=30)
        self.last_evaluated_at = base_time
        return self.next_eligible_at

    def register_change(self, base_category: UpdateCategory) -> None:
        """Si hubo cambios, restablece la cadencia a su categoría base y resetea el contador."""
        self.consecutive_no_change_count = 0
        self.category = base_category
        self.calculate_next_run()

    def register_no_change(self) -> None:
        """Incrementa observaciones consecutivas sin cambios y adapta la frecuencia de manera conservadora."""
        self.consecutive_no_change_count += 1
        # Transición conservadora:
        # Si es diaria y van 7 corridas sin novedades -> semanal
        if self.category == UpdateCategory.DAILY and self.consecutive_no_change_count >= 7:
            self.category = UpdateCategory.WEEKLY
        # Si es semanal y van 4 corridas sin novedades -> mensual
        elif self.category == UpdateCategory.WEEKLY and self.consecutive_no_change_count >= 4:
            self.category = UpdateCategory.MONTHLY

        self.calculate_next_run()
