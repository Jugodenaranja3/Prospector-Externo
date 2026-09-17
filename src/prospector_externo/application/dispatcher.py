"""
Despachador de unidades de trabajo por fuente (SourceDispatcher).
Resuelve el workflow especializado mediante el WorkflowRegistry y coordina su ejecución.
"""

import logging
from prospector_externo.domain.models import SourceConfig
from prospector_externo.kernel.registry import WorkflowRegistry
from prospector_externo.kernel.workflow_port import SourceWorkflow
from prospector_externo.kernel.contracts import ExtractionResult

logger = logging.getLogger("prospector.application.dispatcher")


class SourceDispatcher:
    """Resuelve y ejecuta el workflow especializado correspondiente a la configuración de la fuente."""

    def dispatch(self, config: SourceConfig) -> ExtractionResult:
        """Obtiene el workflow del registro y lo ejecuta sobre la fuente."""
        logger.info(f"Despachando fuente [{config.source_id}] con workflow [{config.workflow}]")
        try:
            workflow = WorkflowRegistry.resolve(config.workflow)
        except ValueError as e:
            logger.error(f"Error resolviendo workflow para [{config.source_id}]: {e}")
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="WORKFLOW_NOT_FOUND",
                error_message=str(e)
            )

        try:
            result = workflow.run(config)
            return result
        except Exception as e:
            logger.critical(f"Excepción no controlada ejecutando workflow [{config.workflow}] en [{config.source_id}]: {e}", exc_info=True)
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="UNHANDLED_WORKFLOW_EXCEPTION",
                error_message=str(e)
            )
