"""Despachador async de workflows por fuente."""

import inspect
import logging
from typing import Optional

from prospector_externo.domain.models import SourceConfig
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.kernel.registry import WorkflowRegistry
import prospector_externo.workflows  # noqa: F401  # garantiza autoregistro sin depender del orden de imports

logger = logging.getLogger("prospector.application.dispatcher")


class SourceDispatcher:
    def __init__(self, http_runtime: Optional[AsyncHttpRuntime] = None) -> None:
        self.http_runtime = http_runtime

    def set_http_runtime(self, runtime: AsyncHttpRuntime) -> None:
        self.http_runtime = runtime

    async def dispatch(self, config: SourceConfig) -> ExtractionResult:
        if self.http_runtime is None:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="HTTP_RUNTIME_NOT_CONFIGURED",
            )

        try:
            workflow = WorkflowRegistry.resolve(config.workflow)
        except ValueError as exc:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="WORKFLOW_NOT_FOUND",
                error_message=str(exc),
            )

        workflow.bind_http_session(self.http_runtime.session_for(config))

        try:
            result = workflow.run(config)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:
            safe_error = ascii(exc)
            logger.error(
                "Fallo no controlado en workflow %s: %s: %s",
                config.workflow,
                type(exc).__name__,
                safe_error,
            )
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="UNHANDLED_WORKFLOW_EXCEPTION",
                error_message=f"{type(exc).__name__}: {safe_error}",
            )
