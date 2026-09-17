"""
Puerto abstracto para la persistencia de Reportes de Corrida (RunReport).
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from prospector_externo.domain.observations import RunReport


class RunReportRepositoryPort(ABC):
    """Puerto de persistencia para los reportes estructurados de corrida."""

    @abstractmethod
    def save_report(self, report: RunReport) -> None:
        """Persiste el reporte estructurado de una corrida."""
        pass

    @abstractmethod
    def get_report(self, run_id: str) -> Optional[RunReport]:
        """Recupera el reporte correspondiente a un run_id."""
        pass

    @abstractmethod
    def list_reports(self, limit: int = 50) -> List[RunReport]:
        """Lista los últimos reportes de corrida generados."""
        pass
