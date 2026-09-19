"""Workflows custom declarativos y acotados para excepciones justificadas."""

import logging
from typing import Any, Dict, List

from prospector_externo.domain.models import (
    DiscoveredUrl,
    DiscoveryType,
    ResourceCandidate,
    SourceConfig,
)
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.domain.observations import CoverageStats
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.workflows.api_workflow import ApiWorkflow
from prospector_externo.workflows.base import BaseWorkflow
from prospector_externo.workflows.html_workflow import HtmlWorkflow

logger = logging.getLogger("prospector.workflows.custom")


class CustomWorkflow(BaseWorkflow):
    """Ejecuta únicamente estrategias custom declaradas en `custom_config`.

    B10C elimina el fallback silencioso en el que `custom` era simplemente un
    alias de HtmlWorkflow. Las excepciones actuales son:
    - `data_endpoint`: endpoint público GET catalogado mediante ApiWorkflow.
    - `download_form_acquisition_job`: formulario público GET-only modelado
      como recurso de adquisición; nunca envía POST.
    """

    @staticmethod
    def _settings(config: SourceConfig) -> Dict[str, Any]:
        return dict(config.custom_config or {})

    @staticmethod
    def _string_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _validate_read_only(settings: Dict[str, Any]) -> str | None:
        methods = {
            method.upper()
            for method in CustomWorkflow._string_list(
                settings.get("allowed_methods", ["GET", "HEAD"])
            )
        }
        disallowed = sorted(methods - {"GET", "HEAD"})
        if disallowed:
            return "CUSTOM_METHOD_NOT_ALLOWED:" + ",".join(disallowed)
        return None

    async def _run_data_endpoint(
        self,
        config: SourceConfig,
        settings: Dict[str, Any],
    ) -> ExtractionResult:
        endpoints = self._string_list(settings.get("data_endpoints"))
        if not endpoints:
            endpoints = list(config.seeds)

        if not endpoints:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="CUSTOM_DATA_ENDPOINT_MISSING",
            )

        api_config = config.model_copy(deep=True)
        api_config.workflow = "api"
        api_config.entrypoint = endpoints[0]
        api_config.seeds = endpoints
        api_config.discover_apis = True
        api_config.probe_api_documentation = False
        api_config.max_api_documents = 0

        workflow = ApiWorkflow()
        workflow.bind_http_session(self._session())

        logger.info(
            "Iniciando CustomWorkflow data_endpoint para [%s] con %d endpoint(s)",
            config.source_id,
            len(endpoints),
        )
        return await workflow.run(api_config)

    async def _run_form_resource(
        self,
        config: SourceConfig,
        settings: Dict[str, Any],
    ) -> ExtractionResult:
        evidence_urls = self._string_list(settings.get("evidence_seed_urls"))
        candidates = evidence_urls or list(config.seeds) or [config.entrypoint]
        url = candidates[0]

        patterns = self._string_list(settings.get("resource_url_patterns"))
        if patterns and not any(pattern in url for pattern in patterns):
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="CUSTOM_FORM_URL_POLICY_MISMATCH",
                error_message=f"URL fuera de resource_url_patterns: {url}",
            )

        session = self._session()
        html, status, error, headers = await session.fetch_document(
            url,
            conditional=False,
            accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        )

        coverage = CoverageStats(
            pages_visited=1,
            requests_total=session.requests_used,
        )

        if error or html is None:
            coverage.urls_failed = 1
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                coverage=coverage,
                failure_code=error or "CUSTOM_FORM_EMPTY_RESPONSE",
                error_message=f"No se pudo verificar el formulario público: {url}",
            )

        if "<form" not in html.casefold():
            coverage.urls_failed = 1
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                coverage=coverage,
                failure_code="CUSTOM_FORM_NOT_CONFIRMED",
                error_message=f"La respuesta GET no contiene un formulario verificable: {url}",
            )

        normalized = UrlNormalizer.normalize(url, base_url=config.entrypoint)
        content_type = ""
        if headers:
            content_type = str(
                headers.get("content-type")
                or headers.get("Content-Type")
                or ""
            ).split(";", 1)[0].strip().lower()

        resource = ResourceCandidate(
            resource_key=UrlNormalizer.compute_resource_key(
                config.source_id,
                normalized,
            ),
            url=normalized,
            source_id=config.source_id,
            title=config.name or "Formulario público de adquisición",
            file_extension="",
            content_type=content_type or None,
            discovered_from_url=normalized,
            raw_url=url,
            discovery_method="custom_form_acquisition_job",
            http_status=status,
        )

        discovered = DiscoveredUrl(
            normalized_url=normalized,
            raw_url=url,
            source_id=config.source_id,
            discovery_type=DiscoveryType.CUSTOM,
            http_status=status,
        )

        coverage.urls_discovered = 1
        coverage.resources_found = 1
        coverage.stop_reason = "CUSTOM_FORM_CONFIRMED"

        logger.info(
            "CustomWorkflow form_resource confirmado para [%s]: %s",
            config.source_id,
            normalized,
        )

        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=[resource],
            discovered_urls=[discovered],
            coverage=coverage,
            metadata={
                "custom_kind": "download_form_acquisition_job",
                "submission_policy": settings.get(
                    "submission_policy",
                    "metadata_only_no_post",
                ),
                "http_policy": "GET_HEAD_ONLY",
            },
        )

    async def run(self, config: SourceConfig) -> ExtractionResult:
        settings = self._settings(config)
        custom_kind = str(settings.get("custom_kind") or "").strip()

        policy_error = self._validate_read_only(settings)
        if policy_error:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code=policy_error,
            )

        if custom_kind == "data_endpoint":
            return await self._run_data_endpoint(config, settings)

        if custom_kind == "download_form_acquisition_job":
            return await self._run_form_resource(config, settings)

        # Compatibilidad explícita para configs custom antiguas durante la
        # migración. Se registra claramente para no confundirlo con un custom
        # especializado.
        logger.warning(
            "CustomWorkflow [%s] sin custom_kind; fallback HTML explícito",
            config.source_id,
        )
        fallback = HtmlWorkflow()
        fallback.bind_http_session(self._session())
        return await fallback.run(config)
