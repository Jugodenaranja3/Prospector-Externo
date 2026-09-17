"""
Puerto abstracto para workflows de descubrimiento (Plugin Architecture).
Cada plugin implementa esta interfaz para aislar sus particularidades de scraping.
"""

from abc import ABC, abstractmethod
from prospector_externo.domain.models import SourceConfig
from prospector_externo.kernel.contracts import ExtractionResult


class SourceWorkflow(ABC):
    """Contrato base que todo plugin de workflow de descubrimiento debe implementar."""

    @abstractmethod
    def run(self, config: SourceConfig) -> ExtractionResult:
        """
        Ejecuta el descubrimiento y prospección sobre la fuente configurada.
        
        Args:
            config: Configuración validada de la fuente.
            
        Returns:
            ExtractionResult con inventario bruto, recursos, URLs y estadísticas de cobertura.
        """
        pass
