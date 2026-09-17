"""
Plugin de workflow para flujos atípicos o excepcionales (CustomWorkflow).
Aplica el patrón Template Method para permitir extensiones puntuales sin modificar el core.
"""

import logging
from prospector_externo.workflows.html_workflow import HtmlWorkflow
from prospector_externo.domain.models import SourceConfig
from prospector_externo.kernel.contracts import ExtractionResult

logger = logging.getLogger("prospector.workflows.custom")


class CustomWorkflow(HtmlWorkflow):
    """Workflow extensible para casos con particularidades no cubiertas por workflows estándar."""

    def pre_crawl_hook(self, config: SourceConfig) -> None:
        """Paso opcional previo a la exploración (ej. negociación de tokens o cookies)."""
        pass

    def post_crawl_hook(self, result: ExtractionResult, config: SourceConfig) -> ExtractionResult:
        """Paso opcional posterior para enriquecimiento específico."""
        return result

    def run(self, config: SourceConfig) -> ExtractionResult:
        logger.info(f"Iniciando CustomWorkflow para fuente: [{config.source_id}]")
        self.pre_crawl_hook(config)
        result = super().run(config)
        return self.post_crawl_hook(result, config)
