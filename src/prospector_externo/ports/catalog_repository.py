"""
Puerto abstracto para el repositorio de catálogo, snapshots y observaciones.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate
from prospector_externo.domain.observations import SourceRunObservation


class CatalogRepositoryPort(ABC):
    """Puerto de persistencia para el catálogo de inventario bruto."""

    @abstractmethod
    def save_snapshot(self, snapshot: Snapshot) -> None:
        """Persiste un snapshot del inventario descubierto."""
        pass

    @abstractmethod
    def get_latest_snapshot(self, source_id: str) -> Optional[Snapshot]:
        """Recupera el último snapshot registrado para una fuente para comparación de cambios."""
        pass

    @abstractmethod
    def save_source_observation(self, observation: SourceRunObservation) -> None:
        """Persiste una observación operativa de una corrida."""
        pass

    @abstractmethod
    def get_source_observations(self, source_id: str, limit: int = 20) -> List[SourceRunObservation]:
        """Obtiene el historial de observaciones de una fuente."""
        pass

    @abstractmethod
    def save_source(self, source: Source) -> None:
        """Registra o actualiza el estado operativo de una fuente."""
        pass

    @abstractmethod
    def get_source(self, source_id: str) -> Optional[Source]:
        """Obtiene la ficha y estado de una fuente por su ID."""
        pass

    @abstractmethod
    def list_sources(self) -> List[Source]:
        """Lista todas las fuentes registradas en el catálogo."""
        pass

    @abstractmethod
    def list_resources(self, source_id: Optional[str] = None) -> List[ResourceCandidate]:
        """Lista recursos descubiertos, opcionalmente filtrados por fuente."""
        pass
