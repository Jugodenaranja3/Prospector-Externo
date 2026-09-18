"""Puerto de workflows del microkernel."""

from abc import ABC, abstractmethod
from prospector_externo.domain.models import SourceConfig
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.infrastructure.http_runtime import SourceHttpSession


class SourceWorkflow(ABC):
    def bind_http_session(self, session: SourceHttpSession) -> None:
        """Inyección explícita de infraestructura por fuente."""
        self.http_session = session

    @abstractmethod
    async def run(self, config: SourceConfig) -> ExtractionResult:
        pass
