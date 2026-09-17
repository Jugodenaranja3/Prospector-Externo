"""
Plugin de workflow para fuentes basadas en API JSON (ApiWorkflow).
"""

import json
import logging
from typing import List
from prospector_externo.workflows.base import BaseWorkflow
from prospector_externo.domain.models import SourceConfig, DiscoveredUrl, DiscoveryType, ResourceCandidate, ChangeStatus
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.domain.observations import CoverageStats

logger = logging.getLogger("prospector.workflows.api")


class ApiWorkflow(BaseWorkflow):
    """Workflow para fuentes que exponen catálogos mediante endpoints JSON."""

    def run(self, config: SourceConfig) -> ExtractionResult:
        logger.info(f"Iniciando ApiWorkflow para fuente: [{config.source_id}]")
        coverage = CoverageStats()
        resources = []
        discovered_urls = []

        endpoint = config.entrypoint
        text, status, err = self.http_client.fetch_html(
            endpoint,
            rate_limit_delay=config.rate_limit_seconds,
            ignore_robots_txt=config.ignore_robots_txt,
            robots_override_reason=config.robots_override_reason
        )

        if err or not text:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code=err or "API_EMPTY",
                error_message=f"Error consultando API: {err}",
                coverage=coverage
            )

        coverage.pages_visited += 1
        discovered_urls.append(DiscoveredUrl(
            normalized_url=endpoint,
            raw_url=endpoint,
            source_id=config.source_id,
            discovery_type=DiscoveryType.API,
            http_status=status
        ))

        try:
            data = json.loads(text)
            # Si el JSON es una lista de objetos con URL
            items = data if isinstance(data, list) else data.get("items", data.get("data", []))
            for item in items:
                if isinstance(item, dict):
                    file_url = item.get("url") or item.get("download_url") or item.get("link")
                    if file_url:
                        norm = UrlNormalizer.normalize(file_url, base_url=endpoint)
                        r_key = UrlNormalizer.compute_url_hash(norm)
                        title = item.get("title") or item.get("name") or "Recurso API"
                        ext = item.get("extension") or ""
                        resources.append(ResourceCandidate(
                            resource_key=r_key,
                            url=norm,
                            source_id=config.source_id,
                            title=title,
                            file_extension=ext,
                            discovered_from_url=endpoint,
                            change_status=ChangeStatus.NEW
                        ))
                        coverage.resources_found += 1
        except Exception as e:
            logger.warning(f"Error parseando respuesta JSON de API {endpoint}: {e}")

        coverage.urls_discovered = len(discovered_urls) + len(resources)
        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=resources,
            discovered_urls=discovered_urls,
            coverage=coverage
        )
